"""End-to-end approval-flow smoke test.

Drives a scout run that will trigger a create_sub_cost_code tool call,
intercepts the approval_request SSE event, decides via POST /approve,
and verifies the run completes with the tool executed (or rejected,
depending on the test variant).

Usage:
    .venv/bin/python scripts/approval_smoke.py             # approve path
    .venv/bin/python scripts/approval_smoke.py --reject    # reject path
    .venv/bin/python scripts/approval_smoke.py --edit      # edit path
    .venv/bin/python scripts/approval_smoke.py --timeout   # timeout (waits ~5 min)

Prereqs:
    API on localhost:8000
    SCOUT_AGENT_USERNAME / SCOUT_AGENT_PASSWORD set
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import config


BASE = "http://localhost:8000"


async def _login(c: httpx.AsyncClient) -> str:
    s = config.Settings()
    r = await c.post(
        "/api/v1/mobile/auth/login",
        json={
            "username": s.scout_agent_username,
            "password": s.scout_agent_password,
        },
    )
    r.raise_for_status()
    return r.json()["data"]["token"]["access_token"]


async def drive(variant: str) -> int:
    # A prompt that induces scout to propose a create. We pass a real
    # CostCode public_id inline so scout has the parent handle.
    prompt = (
        "I want to add a new sub-cost-code. Create `99.99` named "
        "`Test — Approval Smoke` under cost code with "
        "public_id 702D6D89-6B27-432B-AF1A-3B4D7DD5660C (Block Walls). "
        "Aliases should be `smoke-test`."
    )

    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c:
        token = await _login(c)
        auth = {"Authorization": f"Bearer {token}"}

        print(f"── POST /runs  [{variant}]")
        r = await c.post(
            "/api/v1/agents/scout/runs",
            headers=auth,
            json={"user_message": prompt},
        )
        r.raise_for_status()
        pid = r.json()["data"]["session_public_id"]
        print(f"   session: {pid}")

        # Stream events. When we see approval_request, decide based on variant.
        decided = False
        seen_approval_request = False
        saw_tool_call_end = False
        final_done: Optional[dict] = None

        async with c.stream(
            "GET", f"/api/v1/agents/runs/{pid}/events", headers=auth
        ) as stream:
            stream.raise_for_status()
            ev = None
            buf: list[str] = []
            async for line in stream.aiter_lines():
                if line == "":
                    if ev:
                        data = json.loads("".join(buf)) if buf else {}
                        if ev == "approval_request":
                            seen_approval_request = True
                            req_id = data["request_id"]
                            print(f"   ⏸ approval_request: {data['tool_name']}")
                            print(f"     summary: {data['summary']}")
                            print(f"     proposed: {data['proposed_input']}")
                            # Decide based on variant.
                            if variant == "timeout":
                                print("   (not responding; waiting for server timeout)")
                            else:
                                await _decide(c, auth, pid, req_id, data, variant)
                                decided = True
                        elif ev == "approval_decision":
                            print(f"   ✓ approval_decision: {data['decision']}")
                            if data.get("final_input") != data.get("proposed_input"):
                                print(f"     final_input: {data.get('final_input')}")
                        elif ev == "tool_call_start":
                            print(f"   → tool_call_start: {data['name']}({data.get('input')})")
                        elif ev == "tool_call_end":
                            saw_tool_call_end = True
                            r = data.get("result", {})
                            marker = "✗" if r.get("is_error") else "✓"
                            content = str(r.get("content", ""))[:200]
                            print(f"   {marker} tool_call_end: {content}")
                        elif ev == "done":
                            final_done = data
                            print(f"   done: reason={data['reason']} tokens in/out={data['usage']['input_tokens']}/{data['usage']['output_tokens']}")
                            if data.get("cost_usd") is not None:
                                print(f"     cost: ${data['cost_usd']:.4f}")
                            break
                        elif ev == "error":
                            print(f"   error: {data.get('message')}")
                            break
                    ev = None
                    buf = []
                elif line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    buf.append(line[5:].lstrip())

        # Post-run checks
        print()
        print("── verdict")
        if variant == "approve":
            assert seen_approval_request, "expected approval_request"
            assert decided, "expected to decide"
            assert saw_tool_call_end, "expected tool_call_end"
            print("   approve path: PASS")
            return 0
        if variant == "reject":
            assert seen_approval_request, "expected approval_request"
            assert decided, "expected to decide"
            assert saw_tool_call_end, "expected tool_call_end (synthetic rejection result)"
            print("   reject path: PASS")
            return 0
        if variant == "edit":
            assert seen_approval_request, "expected approval_request"
            assert decided, "expected to decide"
            assert saw_tool_call_end, "expected tool_call_end"
            print("   edit path: PASS")
            return 0
        if variant == "timeout":
            assert seen_approval_request, "expected approval_request"
            assert saw_tool_call_end, "expected tool_call_end (synthetic timeout result)"
            print("   timeout path: PASS")
            return 0
        return 1


async def _decide(
    c: httpx.AsyncClient,
    auth: dict,
    pid: str,
    request_id: str,
    approval_req: dict,
    variant: str,
) -> None:
    if variant == "approve":
        body = {"request_id": request_id, "decision": "approve"}
    elif variant == "reject":
        body = {"request_id": request_id, "decision": "reject"}
    elif variant == "edit":
        proposed = dict(approval_req.get("proposed_input") or {})
        proposed["name"] = (proposed.get("name") or "") + " (edited)"
        body = {
            "request_id": request_id,
            "decision": "edit",
            "edited_input": proposed,
        }
    else:
        raise ValueError(f"unknown variant {variant!r}")
    print(f"   → POST /approve  {body}")
    r = await c.post(
        f"/api/v1/agents/runs/{pid}/approve", headers=auth, json=body
    )
    r.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reject", action="store_true")
    group.add_argument("--edit", action="store_true")
    group.add_argument("--timeout", action="store_true")
    args = parser.parse_args()
    variant = (
        "reject" if args.reject
        else "edit" if args.edit
        else "timeout" if args.timeout
        else "approve"
    )
    return asyncio.run(drive(variant))


if __name__ == "__main__":
    sys.exit(main())
