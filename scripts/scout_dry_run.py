"""End-to-end dry run for the scout agent.

Requires the FastAPI server to be running (scout calls its own API over
HTTP). Default base URL is http://localhost:8000 (override via
INTERNAL_API_BASE_URL env var).

Usage:
    # First, in another terminal:
    .venv/bin/uvicorn app:app --reload --port 8000

    # Then:
    .venv/bin/python scripts/scout_dry_run.py "What is sub-cost-code 10.01?"
    .venv/bin/python scripts/scout_dry_run.py -v "List all sub-cost-codes"

Prerequisites:
  - ANTHROPIC_API_KEY in .env
  - SCOUT_AGENT_USERNAME / SCOUT_AGENT_PASSWORD in .env matching a real
    user in the database
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Triggers tool + agent registration.
import intelligence.agents.scout  # noqa: F401
from intelligence.run import run_agent


async def drive(prompt: str, verbose: bool) -> int:
    session_public_id_holder: dict[str, Optional[str]] = {"public_id": None}

    def _record_session_created(sess):
        session_public_id_holder["public_id"] = sess.public_id
        if not verbose:
            print(f"── session: {sess.public_id}")

    if verbose:
        print(f"[scout] prompt: {prompt!r}")
        print("[scout] running ...")
    else:
        print(f"── prompt: {prompt}")

    errored = False
    async for ev in run_agent(
        name="scout",
        user_message=prompt,
        on_session_created=_record_session_created,
    ):
        if verbose:
            print(f"  {type(ev).__name__}: {ev.model_dump()}")
            continue

        if ev.type == "turn_start":
            print(f"\n── turn {ev.turn} ({ev.model}) ──")
        elif ev.type == "text_delta":
            print(ev.text, end="", flush=True)
        elif ev.type == "tool_call_start":
            print(f"\n  → calling {ev.name}({ev.input})", flush=True)
        elif ev.type == "tool_call_end":
            marker = "✗" if ev.result.is_error else "✓"
            output_preview = (
                ev.result.content[:200] + "…"
                if isinstance(ev.result.content, str) and len(ev.result.content) > 200
                else ev.result.content
            )
            print(f"  {marker} {ev.name} → {output_preview}", flush=True)
        elif ev.type == "turn_end":
            pass
        elif ev.type == "done":
            print(
                f"\n── done │ reason={ev.reason} │ "
                f"tokens in/out = {ev.usage.input_tokens}/{ev.usage.output_tokens} ──"
            )
        elif ev.type == "error":
            print(f"\n[scout] ERROR: {ev.message} (code={ev.code})", file=sys.stderr)
            errored = True

    return 1 if errored else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scout agent dry run")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.prompt:
        prompt = " ".join(args.prompt)
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        parser.error("provide a prompt as argument or via stdin")
        return 2

    if not prompt:
        parser.error("empty prompt")
        return 2

    return asyncio.run(drive(prompt, args.verbose))


if __name__ == "__main__":
    sys.exit(main())
