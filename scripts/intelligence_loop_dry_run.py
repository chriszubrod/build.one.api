"""Interactive dry run for the intelligence tool-calling loop.

Default: runs through session_runner so every run is durably persisted
(AgentSession, AgentTurn, AgentToolCall). Use --no-persist to bypass
persistence for quick, DB-free tests.

Registers the builtin tools (`now`, `add`) and runs a single agent loop
with whatever prompt you pass.

Examples:
    python scripts/intelligence_loop_dry_run.py "What time is it?"
    python scripts/intelligence_loop_dry_run.py --no-persist "Add 2 and 3"
    python scripts/intelligence_loop_dry_run.py -v "Say hi"
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importing builtins registers `now` and `add` in the global tool registry.
import intelligence.tools.builtins  # noqa: F401
from intelligence.loop.runner import run
from intelligence.loop.session_runner import run_session
from intelligence.loop.termination import BudgetPolicy
from intelligence.tools import registry as tool_registry
from intelligence.tools.base import ToolContext
from intelligence.transport.registry import get_transport


DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_PROVIDER = "anthropic"
DEFAULT_AGENT_NAME = "dry-run"
DEFAULT_TOOLS = ["now", "add"]


async def drive(
    prompt: str,
    model: str,
    system: Optional[str],
    verbose: bool,
    persist: bool,
) -> int:
    transport = get_transport(DEFAULT_PROVIDER)
    tools = tool_registry.resolve(DEFAULT_TOOLS)
    ctx = ToolContext(
        agent_id=None,
        auth_token=None,
        session_id="dry-run",
        requesting_user_id=None,
    )
    budget = BudgetPolicy(max_turns=8, max_tokens=50_000)

    if verbose:
        print(f"[loop] model={model}")
        print(f"[loop] tools={[t.name for t in tools]}")
        print(f"[loop] persist={persist}")
        if system:
            print(f"[loop] system={system!r}")
        print(f"[loop] prompt={prompt!r}")
        print("[loop] running ...")
    else:
        print(f"── prompt: {prompt}")

    if persist:
        session_public_id_holder: dict[str, Optional[str]] = {"public_id": None}

        def _record_session_created(sess):
            session_public_id_holder["public_id"] = sess.public_id
            if not verbose:
                print(f"── session: {sess.public_id}")

        stream = run_session(
            transport=transport,
            provider=DEFAULT_PROVIDER,
            agent_name=DEFAULT_AGENT_NAME,
            model=model,
            user_message=prompt,
            tools=tools,
            ctx=ctx,
            system=system,
            budget=budget,
            on_session_created=_record_session_created,
        )
    else:
        stream = run(
            transport=transport,
            model=model,
            user_message=prompt,
            tools=tools,
            ctx=ctx,
            system=system,
            budget=budget,
        )

    errored = False
    async for ev in stream:
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
            print(f"  {marker} {ev.name} → {ev.result.content}", flush=True)
        elif ev.type == "turn_end":
            pass  # detailed info lives in verbose mode
        elif ev.type == "done":
            print(
                f"\n── done │ reason={ev.reason} │ "
                f"tokens in/out = {ev.usage.input_tokens}/{ev.usage.output_tokens} ──"
            )
        elif ev.type == "error":
            print(f"\n[loop] ERROR: {ev.message} (code={ev.code})", file=sys.stderr)
            errored = True

    return 1 if errored else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Intelligence loop dry run")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--system", default=None)
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip DB persistence (faster; no AgentSession row created)",
    )
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

    return asyncio.run(
        drive(prompt, args.model, args.system, args.verbose, persist=not args.no_persist)
    )


if __name__ == "__main__":
    sys.exit(main())
