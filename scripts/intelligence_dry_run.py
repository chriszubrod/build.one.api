"""Interactive dry run for the intelligence Level 1 pipeline.

Pass a prompt as an argument, via stdin, or use the built-in default.

Examples:
    python scripts/intelligence_dry_run.py "What is 2+2?"
    python scripts/intelligence_dry_run.py --system "You are a pirate" "Tell me about ships"
    python scripts/intelligence_dry_run.py --model claude-sonnet-4-6 "Write a haiku"
    echo "Hello there" | python scripts/intelligence_dry_run.py
    python scripts/intelligence_dry_run.py -v "Say hi"          # show raw events
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence.messages.types import Message, Text
from intelligence.transport.registry import get_transport


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


async def run(
    prompt: str,
    model: str,
    system: Optional[str],
    max_tokens: int,
    verbose: bool,
) -> int:
    transport = get_transport("anthropic")
    messages = [Message(role="user", content=[Text(text=prompt)])]

    if verbose:
        print(f"[dry-run] model={model}")
        if system:
            print(f"[dry-run] system={system!r}")
        print(f"[dry-run] prompt={prompt!r}")
        print("[dry-run] streaming ...")
    else:
        print("── response ──")

    stop_reason: Optional[str] = None
    usage = None
    error = None

    async for ev in transport.stream(
        messages=messages, model=model, system=system, max_tokens=max_tokens
    ):
        if verbose:
            print(f"  {type(ev).__name__}: {ev.model_dump()}")
            continue

        if ev.type == "text_delta":
            print(ev.text, end="", flush=True)
        elif ev.type == "turn_end":
            stop_reason = ev.stop_reason
        elif ev.type == "done":
            usage = ev.usage
        elif ev.type == "error":
            error = ev

    if not verbose:
        print()  # newline after streamed text

    if error:
        print(f"\n[dry-run] ERROR: {error.message} (code={error.code})", file=sys.stderr)
        return 1

    if not verbose and usage is not None:
        print(
            f"── {stop_reason} │ tokens in/out = {usage.input_tokens}/{usage.output_tokens} ──"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Intelligence Level 1 dry run")
    parser.add_argument("prompt", nargs="*", help="Prompt text (reads stdin if omitted)")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Model ID (default: {DEFAULT_MODEL})"
    )
    parser.add_argument("--system", default=None, help="Optional system prompt")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show raw canonical events"
    )
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

    return asyncio.run(
        run(prompt, args.model, args.system, args.max_tokens, args.verbose)
    )


if __name__ == "__main__":
    sys.exit(main())
