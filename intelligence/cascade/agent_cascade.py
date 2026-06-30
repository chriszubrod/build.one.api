"""Agent-loop cascade — run a whole tool-using agent cheapest-first.

Runs an agent's prompt + tools + identity on the cheapest rung's model; if the
run fails the gate (errored / didn't finish / produced no valid structured
result / confidence < τ), escalate to the next, more expensive model — up to
Claude. Same ladder + validation+confidence gate as the single-shot cascade,
but each rung is a full multi-turn `run_agent` instead of one completion.

⚠️ SCOPE — read-only / structured-output agents only.
Escalation RE-RUNS the agent on a new model, so this is safe only for agents
whose tools are READ-ONLY (no entity mutations) and whose final message is a
structured `{...,"confidence":float}` answer. Running a SIDE-EFFECTING agent
(e.g. email_specialist, which creates Bills) through this would double its side
effects on every escalation. The pattern for side-effecting work is
"decide cheap, act once" — run a read-only DECISION agent through the cascade,
then perform the mutation a single time after acceptance. That separation is a
documented follow-up; this module deliberately does not attempt it.
"""
import logging
from typing import Any, Awaitable, Callable, Optional

from intelligence.cascade.core import (
    CascadeResult,
    DEFAULT_LADDER,
    Rung,
    _coerce_confidence,
    _parse_json_object,
    run_ladder,
)
from intelligence.transport.base import Usage

logger = logging.getLogger(__name__)

# (final_text) -> (result_dict | None, confidence | None)
ExtractResult = Callable[[str], tuple[Optional[dict[str, Any]], Optional[float]]]


def _default_extract(final_text: str) -> tuple[Optional[dict[str, Any]], Optional[float]]:
    """Parse the agent's final message as JSON `{..., "confidence": float}`."""
    parsed = _parse_json_object(final_text)
    if parsed is None:
        return None, None
    return parsed, _coerce_confidence(parsed.get("confidence"))


async def run_agent_cascade(
    *,
    agent_name: str,
    user_message: str,
    validate: Callable[[dict[str, Any]], tuple[bool, str]],
    threshold: float,
    extract_result: Optional[ExtractResult] = None,
    ladder: Optional[tuple[Rung, ...]] = None,
    requesting_user_id: Optional[int] = None,
    run_agent_fn: Optional[Callable[..., Any]] = None,
) -> CascadeResult:
    """Run `agent_name` cheapest-first, escalating until the run-level gate
    passes (finished cleanly AND validator passes AND confidence ≥ τ).

    `run_agent_fn` is injectable for testing; defaults to the real
    `intelligence.run.run_agent`. See the module docstring's read-only scope.
    """
    rungs = ladder or DEFAULT_LADDER
    extract = extract_result or _default_extract
    runner = run_agent_fn
    if runner is None:
        from intelligence.run import run_agent as runner  # lazy: avoids import cycle

    async def execute(rung: Rung):
        return await _run_agent_rung(
            runner, agent_name, rung, user_message, extract, requesting_user_id,
        )

    return await run_ladder(rungs, execute, validate, threshold, f"agent:{agent_name}")


async def _run_agent_rung(
    runner: Callable[..., Any],
    agent_name: str,
    rung: Rung,
    user_message: str,
    extract: ExtractResult,
    requesting_user_id: Optional[int],
) -> tuple[Optional[dict[str, Any]], Optional[float], Usage, Optional[str]]:
    """Run one agent attempt on the rung's model; return (result, confidence,
    usage, error). The agent's FINAL-turn text is what `extract` parses."""
    turn_text: list[str] = []   # accumulates the in-flight turn
    final_text = ""             # last completed turn's text = the final answer
    usage = Usage()
    error: Optional[str] = None
    try:
        async for ev in runner(
            name=agent_name, user_message=user_message,
            provider=rung.provider, model=rung.model,
            requesting_user_id=requesting_user_id,
        ):
            t = getattr(ev, "type", None)
            if t == "turn_start":
                turn_text = []
            elif t == "text_delta":
                turn_text.append(ev.text)
            elif t == "turn_end":
                final_text = "".join(turn_text)
            elif t == "approval_request":
                # A read-only cascade agent shouldn't hit an approval gate; if
                # it does, the run can't complete here — fail this rung.
                error = "agent paused on an approval gate (cascade requires a read-only agent)"
                break
            elif t == "done":
                usage = ev.usage
            elif t == "error":
                error = f"{ev.code}: {ev.message}" if getattr(ev, "code", None) else ev.message
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    if error:
        return None, None, usage, error
    result, confidence = extract(final_text)
    if result is None:
        return None, None, usage, None
    return result, confidence, usage, None
