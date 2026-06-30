"""Live Phase-3 demo: run the read-only email-triage agent through the model
cascade on a real EmailMessage, cheapest-first.

The agent loop + cascade run LOCALLY (in this process); the agent's read-only
tools (read_email_message, search_email_sender_history) hit the API at
INTERNAL_API_BASE_URL — default the PROD app, so it reads a real prod email.
The classification itself runs on Foundry (DeepSeek/GPT-5.4) then Anthropic,
escalating until a rung clears the validation+confidence gate.

Usage:
    .venv/bin/python scripts/triage_cascade_dry_run.py <email_public_id>
    .venv/bin/python scripts/triage_cascade_dry_run.py <id> --threshold 0.9
    .venv/bin/python scripts/triage_cascade_dry_run.py <id> --base-url http://localhost:8000

Prerequisites (.env):
  - FOUNDRY_API_KEY + FOUNDRY_ENDPOINT (Foundry rungs) and ANTHROPIC_API_KEY
    (fallback rungs).
  - EMAIL_AGENT_USERNAME / EMAIL_AGENT_PASSWORD matching a real prod user
    (the triage agent reuses the email_agent identity).
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_PROD = "https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net"


async def drive(public_id: str, threshold: float) -> int:
    # Imported after INTERNAL_API_BASE_URL is set so the agent's tool calls
    # resolve against the chosen API.
    import intelligence.agents.email_triage_specialist  # noqa: F401  (registers the agent)
    from intelligence.cascade import run_agent_cascade
    from intelligence.cascade.email_classification import (
        EMAIL_CLASSIFY_THRESHOLD,
        _validate,
    )

    user_message = (
        "Triage and classify this email. Read it (and the sender history if "
        f"useful), then return the JSON classification. EmailMessage "
        f"public_id: {public_id}"
    )

    print(f"── triage cascade on EmailMessage {public_id}")
    print(f"── API: {os.environ.get('INTERNAL_API_BASE_URL')}  τ={threshold}\n")

    result = await run_agent_cascade(
        agent_name="email_triage_specialist",
        user_message=user_message,
        validate=_validate,
        threshold=threshold,
    )

    print("── per-rung attempts (cheapest-first):")
    for a in result.attempts:
        cls = (a.result or {}).get("classification")
        line = (
            f"   {a.rung.provider:9}/{a.rung.model:28} "
            f"{'ACCEPT' if a.accepted else 'escalate':8} "
            f"validated={str(a.validated):5} conf={a.confidence} "
            f"class={cls} {a.latency_ms}ms "
            f"tok(in={a.usage.input_tokens},out={a.usage.output_tokens})"
        )
        if a.error:
            line += f" ERR={a.error}"
        print(line)

    print()
    if result.accepted:
        r = result.result or {}
        print(
            f"✅ ACCEPTED at {result.winning_rung.provider}/{result.winning_rung.model}: "
            f"{r.get('classification')} (confidence {result.confidence})"
        )
        print(f"   reason: {r.get('reason')}")
    else:
        r = result.result or {}
        print(
            "⚠️  NEEDS HUMAN — no rung cleared the gate. Best attempt: "
            f"{(result.winning_rung.model if result.winning_rung else None)} "
            f"-> {r.get('classification')} (conf {result.confidence})"
        )
    return 0 if result.accepted else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Email-triage model-cascade dry run")
    parser.add_argument("public_id", help="EmailMessage public_id to triage")
    parser.add_argument("--threshold", type=float, default=None,
                        help="acceptance confidence threshold (default: task default)")
    parser.add_argument("--base-url", default=None,
                        help=f"INTERNAL_API_BASE_URL for the agent's tool calls (default: prod {_PROD})")
    args = parser.parse_args()

    # Point the agent's tool calls at the chosen API before importing anything
    # that reads config.
    os.environ["INTERNAL_API_BASE_URL"] = args.base_url or os.environ.get("INTERNAL_API_BASE_URL") or _PROD

    threshold = args.threshold
    if threshold is None:
        from intelligence.cascade.email_classification import EMAIL_CLASSIFY_THRESHOLD
        threshold = EMAIL_CLASSIFY_THRESHOLD

    return asyncio.run(drive(args.public_id, threshold))


if __name__ == "__main__":
    raise SystemExit(main())
