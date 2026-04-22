"""Scout evaluation harness.

Run a suite of representative prompts against scout on a local API and
check behavior: which tools got called, what the final answer contains,
what it must NOT contain, plus token/latency stats.

Prereqs:
  - Local API running on http://localhost:8000
  - SCOUT_AGENT_USERNAME / SCOUT_AGENT_PASSWORD in .env matching a real
    user in the database

Usage:
    .venv/bin/python scripts/scout_eval.py
    .venv/bin/python scripts/scout_eval.py --case lookup_by_number  # single case
    .venv/bin/python scripts/scout_eval.py -v                        # verbose

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

import config


BASE_URL = "http://localhost:8000"


# ─── Test cases ──────────────────────────────────────────────────────────

@dataclass
class Case:
    name: str
    prompt: str
    required_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    required_phrases: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=list)
    required_phrases_any: list[str] = field(default_factory=list)  # at least one must match
    max_turns: Optional[int] = None


CASES: list[Case] = [
    Case(
        name="lookup_by_number",
        prompt="What is sub-cost-code 10.01?",
        required_tools=["read_sub_cost_code_by_number", "read_cost_code_by_id"],
        required_phrases=["10.01", "Block"],
        forbidden_phrases=["cost code 11"],  # internal id leak — ids differ from numbers
    ),
    Case(
        name="search_by_name_hint",
        prompt="What's the sub-cost-code for concrete footers?",
        required_tools=["search_sub_cost_codes"],
        required_phrases=["9.01", "Footers"],
    ),
    Case(
        name="search_by_number_prefix",
        prompt="What sub-cost-codes start with 10.0?",
        required_tools=["search_sub_cost_codes"],
        required_phrases=["10.00", "10.01"],
    ),
    Case(
        name="alias_lookup",
        prompt="What sub-cost-code has the alias '01.1'?",
        required_phrases=["1.01"],
        # Scout may pick either search or read_by_alias; both are acceptable.
    ),
    Case(
        name="not_found_graceful",
        prompt="What is sub-cost-code 999.99?",
        required_tools=["read_sub_cost_code_by_number"],
        required_phrases_any=[
            "not found", "does not exist", "no such", "cannot find",
            "couldn't find", "no sub-cost-code", "not a valid",
        ],
    ),
    Case(
        name="out_of_scope_vendor",
        prompt="Who is vendor ABC Construction?",
        forbidden_tools=[
            "list_sub_cost_codes",
            "search_sub_cost_codes",
            "read_sub_cost_code_by_number",
            "read_sub_cost_code_by_public_id",
            "read_sub_cost_code_by_alias",
        ],
        required_phrases_any=[
            "wired up", "not available", "not yet", "don't have",
            "do not have", "no vendor tools", "vendor tool",
        ],
    ),
    Case(
        name="catalog_count",
        prompt="How many sub-cost-codes are there in total?",
        required_tools=["list_sub_cost_codes"],
    ),
    Case(
        name="parent_cost_code_name",
        prompt="What is 9.01 and what is its parent cost code?",
        required_tools=["read_cost_code_by_id"],
        required_phrases=["9.01", "Footers"],
        forbidden_phrases=["cost code 10"],  # 10 is the internal id; CostCode.number is "09"
    ),
]


# ─── Runner ──────────────────────────────────────────────────────────────

@dataclass
class Result:
    case: Case
    ok: bool
    failures: list[str]
    tools_called: list[str]
    turn_count: int
    answer: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    duration_s: float


async def login(c: httpx.AsyncClient) -> str:
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


async def run_case(
    c: httpx.AsyncClient, auth: dict[str, str], case: Case, verbose: bool
) -> Result:
    start = time.time()
    r = await c.post(
        "/api/v1/agents/scout/runs",
        headers=auth,
        json={"user_message": case.prompt},
    )
    r.raise_for_status()
    pid = r.json()["data"]["session_public_id"]

    tools_called: list[str] = []
    turn_count = 0
    answer = ""
    input_tokens = output_tokens = cache_read = cache_write = 0

    async with c.stream(
        "GET", f"/api/v1/agents/runs/{pid}/events", headers=auth
    ) as stream:
        stream.raise_for_status()
        event_name: Optional[str] = None
        buf: list[str] = []
        async for line in stream.aiter_lines():
            if line == "":
                if event_name:
                    d = json.loads("".join(buf)) if buf else {}
                    if event_name == "turn_start":
                        turn_count += 1
                    elif event_name == "tool_call_start":
                        tools_called.append(d.get("name", ""))
                    elif event_name == "text_delta":
                        answer += d.get("text", "")
                    elif event_name == "done":
                        u = d.get("usage") or {}
                        input_tokens = u.get("input_tokens", 0)
                        output_tokens = u.get("output_tokens", 0)
                        cache_read = u.get("cache_read_input_tokens", 0)
                        cache_write = u.get("cache_creation_input_tokens", 0)
                        break
                    elif event_name == "error":
                        answer += f"\n[ERROR: {d.get('message')}]"
                        break
                event_name = None
                buf = []
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                buf.append(line[5:].lstrip())

    duration = time.time() - start
    failures = evaluate(case, tools_called, answer, turn_count)
    if verbose:
        print(f"  [{case.name}] tools={tools_called}")
        print(f"  [{case.name}] answer={answer[:200]!r}")

    return Result(
        case=case,
        ok=len(failures) == 0,
        failures=failures,
        tools_called=tools_called,
        turn_count=turn_count,
        answer=answer,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        duration_s=duration,
    )


def evaluate(
    case: Case, tools: list[str], answer: str, turn_count: int
) -> list[str]:
    failures: list[str] = []
    tool_set = set(tools)
    ans = answer.lower()

    for required in case.required_tools:
        if required not in tool_set:
            failures.append(f"required tool not called: {required}")

    for forbidden in case.forbidden_tools:
        if forbidden in tool_set:
            failures.append(f"forbidden tool was called: {forbidden}")

    for phrase in case.required_phrases:
        if phrase.lower() not in ans:
            failures.append(f"answer missing phrase: {phrase!r}")

    for phrase in case.forbidden_phrases:
        if phrase.lower() in ans:
            failures.append(f"answer contains forbidden phrase: {phrase!r}")

    if case.required_phrases_any:
        if not any(p.lower() in ans for p in case.required_phrases_any):
            failures.append(
                "answer missing any of: "
                + ", ".join(repr(p) for p in case.required_phrases_any)
            )

    if case.max_turns is not None and turn_count > case.max_turns:
        failures.append(f"turn_count {turn_count} exceeded max_turns {case.max_turns}")

    return failures


# ─── Output formatting ───────────────────────────────────────────────────

def format_result(r: Result) -> str:
    icon = "✓" if r.ok else "✗"
    tok = f"{r.input_tokens}/{r.output_tokens}"
    if r.cache_read or r.cache_write:
        tok += f" (cache r/w {r.cache_read}/{r.cache_write})"
    tools_str = ",".join(r.tools_called) or "(none)"
    line = (
        f"  {icon} {r.case.name:<32}"
        f"  {r.turn_count} turn(s)"
        f"  tokens {tok}"
        f"  {r.duration_s:.1f}s"
    )
    extra = ""
    if r.failures:
        extra = "\n    failures:"
        for f in r.failures:
            extra += f"\n      • {f}"
        extra += f"\n    tools called: {tools_str}"
    return line + extra


# ─── Entry point ─────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> int:
    cases = CASES
    if args.case:
        cases = [c for c in CASES if c.name == args.case]
        if not cases:
            print(f"No case named {args.case!r}")
            return 2

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180) as c:
        try:
            token = await login(c)
        except Exception as exc:
            print(f"Login failed: {exc}", file=sys.stderr)
            print(
                "\nEnsure the API is running on localhost:8000 and "
                "SCOUT_AGENT_USERNAME / SCOUT_AGENT_PASSWORD are set in .env",
                file=sys.stderr,
            )
            return 2
        auth = {"Authorization": f"Bearer {token}"}

        print("Scout Evaluation Harness")
        print("=" * 72)
        print()

        results: list[Result] = []
        for case in cases:
            r = await run_case(c, auth, case, args.verbose)
            results.append(r)
            print(format_result(r))

        print()
        print("─" * 72)
        passed = sum(1 for r in results if r.ok)
        total = len(results)
        total_in = sum(r.input_tokens for r in results)
        total_out = sum(r.output_tokens for r in results)
        total_cache_r = sum(r.cache_read for r in results)
        total_cache_w = sum(r.cache_write for r in results)
        total_time = sum(r.duration_s for r in results)
        print(
            f"  {passed}/{total} passed"
            f"  ·  tokens in/out {total_in}/{total_out}"
            f"  ·  cache r/w {total_cache_r}/{total_cache_w}"
            f"  ·  {total_time:.1f}s"
        )
        return 0 if passed == total else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scout evaluation harness")
    parser.add_argument(
        "--case", help="Run only the case with this name", default=None
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args())))
