"""Level 1 smoke test: stream a short completion through the canonical pipeline.

Requires ANTHROPIC_API_KEY in the environment (or .env).

Run:
    python scripts/smoke_intelligence_level1.py
"""
import asyncio
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence.messages.types import Message, Text
from intelligence.transport.registry import get_transport


MODEL = "claude-haiku-4-5-20251001"  # cheapest + fastest for smoke testing


async def main() -> int:
    transport = get_transport("anthropic")
    messages = [
        Message(role="user", content=[Text(text="Reply with exactly: hello, world")]),
    ]

    print(f"[smoke] model={MODEL}")
    print("[smoke] streaming ...")

    saw_done = False
    saw_error = False
    async for ev in transport.stream(messages=messages, model=MODEL, max_tokens=64):
        print(f"  {type(ev).__name__}: {ev.model_dump()}")
        if ev.type == "done":
            saw_done = True
        if ev.type == "error":
            saw_error = True

    if saw_error:
        print("[smoke] FAILED — saw an error event")
        return 1
    if not saw_done:
        print("[smoke] FAILED — stream ended without a Done event")
        return 1
    print("[smoke] OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
