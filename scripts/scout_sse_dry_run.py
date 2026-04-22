"""End-to-end SSE dry run for scout via the new HTTP surface.

Authenticates as a real user (via the mobile login endpoint), POSTs a run,
then streams events back via Server-Sent Events. Prints each event as it
arrives.

Requires:
  - FastAPI server running (uvicorn app:app --port 8000)
  - A user in the DB with credentials to log in as
  - Env vars SSE_TEST_USERNAME / SSE_TEST_PASSWORD (the *requesting user*)
    or fall back to SCOUT_AGENT_USERNAME / SCOUT_AGENT_PASSWORD for a quick
    local test.

Usage:
    python scripts/scout_sse_dry_run.py "What is sub-cost-code 10.01?"
    python scripts/scout_sse_dry_run.py --agent scout "List some sub-cost-codes"
    python scripts/scout_sse_dry_run.py --disconnect-at 2 "prompt"   # stop stream after 2 events
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

import config


DEFAULT_BASE_URL = os.environ.get("INTERNAL_API_BASE_URL", "http://localhost:8000")


async def _login(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/mobile/auth/login",
        json={"username": username, "password": password},
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["data"]["token"]["access_token"]
    return token


async def drive(
    prompt: str,
    agent: str,
    disconnect_at: Optional[int],
    base_url: str,
) -> int:
    settings = config.Settings()
    username = (
        os.environ.get("SSE_TEST_USERNAME")
        or settings.scout_agent_username
    )
    password = (
        os.environ.get("SSE_TEST_PASSWORD")
        or settings.scout_agent_password
    )
    if not username or not password:
        print(
            "error: SSE_TEST_USERNAME / SSE_TEST_PASSWORD (or SCOUT_AGENT_*) not set",
            file=sys.stderr,
        )
        return 2

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        # 1. Log in as the requesting user.
        token = await _login(client, username, password)
        auth_header = {"Authorization": f"Bearer {token}"}

        # 2. POST the run.
        print(f"── POST /api/v1/agents/{agent}/runs")
        resp = await client.post(
            f"/api/v1/agents/{agent}/runs",
            headers=auth_header,
            json={"user_message": prompt},
        )
        if resp.status_code != 200:
            print(
                f"run start failed: HTTP {resp.status_code}: {resp.text}",
                file=sys.stderr,
            )
            return 1
        body = resp.json()
        public_id = body["data"]["session_public_id"]
        print(f"── session: {public_id}")

        # 3. Stream events.
        print(f"── GET /api/v1/agents/runs/{public_id}/events")
        event_count = 0
        async with client.stream(
            "GET",
            f"/api/v1/agents/runs/{public_id}/events",
            headers=auth_header,
        ) as stream:
            stream.raise_for_status()
            event_name: Optional[str] = None
            data_parts: list[str] = []
            async for line in stream.aiter_lines():
                if line == "":
                    if event_name is not None:
                        raw = "".join(data_parts)
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            data = raw
                        event_count += 1
                        print(f"  [{event_count}] {event_name}: {data}")
                        if disconnect_at is not None and event_count >= disconnect_at:
                            print(f"── disconnecting after {event_count} events")
                            return 0
                        if event_name in ("done", "error"):
                            return 0
                    event_name = None
                    data_parts = []
                elif line.startswith("event:"):
                    event_name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_parts.append(line[len("data:") :].lstrip())
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scout SSE dry run")
    parser.add_argument("prompt", nargs="+")
    parser.add_argument("--agent", default="scout")
    parser.add_argument(
        "--disconnect-at",
        type=int,
        default=None,
        help="Disconnect after receiving N events (to test reconnection)",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    prompt = " ".join(args.prompt)
    return asyncio.run(
        drive(prompt, args.agent, args.disconnect_at, args.base_url)
    )


if __name__ == "__main__":
    sys.exit(main())
