"""Model cascade core — the ladder, the acceptance gate, and the runner.

Single-shot scope (v1): each rung is ONE structured completion (no tools).
The transport's `stream()` is driven with `tools=None`; text deltas are
collected and parsed as JSON `{...task fields..., "confidence": float}`.

The acceptance gate is the locked design: **validation AND confidence**.
A rung is accepted iff the task's deterministic validator passes AND the
model's self-reported confidence ≥ the task threshold τ. Either failing
escalates to the next (more expensive) rung.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from intelligence.messages.types import Message, Text
from intelligence.transport import registry as transport_registry
from intelligence.transport.base import Done, TextDelta, TransportError, Usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rung:
    """One step on the ladder: a provider + the model/deployment name it runs."""
    provider: str          # transport-registry key: "foundry" | "anthropic"
    model: str             # deployment name (Foundry) / model id (Anthropic)
    max_tokens: int = 1024


# Cheapest-first, cost-confirmed 2026-06-30 (blended $/1M: DeepSeek ~0.21,
# nano ~0.73, mini ~2.63, Haiku 3.00, Sonnet 9.00). Foundry model strings must
# match the actual Foundry deployment names — VERIFY when the endpoint is set.
DEFAULT_LADDER: tuple[Rung, ...] = (
    Rung("foundry", "DeepSeek-V4-Flash"),
    Rung("foundry", "gpt-5.4-nano"),
    Rung("foundry", "gpt-5.4-mini"),
    Rung("anthropic", "claude-haiku-4-5-20251001"),
    Rung("anthropic", "claude-sonnet-4-6"),
)


@dataclass(frozen=True)
class StructuredTask:
    """A discrete classify/extract/score task the cascade can run.

    `build_user_message` turns the caller's payload into the user-turn text.
    `validate` is the deterministic hard gate: it receives the parsed result
    dict and returns (ok, reason). The system prompt must instruct the model
    to answer as JSON including a numeric `confidence` in [0, 1].
    """
    name: str
    system_prompt: str
    threshold: float
    build_user_message: Callable[[Any], str]
    validate: Callable[[dict[str, Any]], tuple[bool, str]]
    ladder: Optional[tuple[Rung, ...]] = None
    # Generation params passed to every rung as a superset; each transport keeps
    # only what its model family accepts (temperature for non-reasoning models,
    # reasoning_effort for reasoning models). Default = deterministic + cheap,
    # which is what classify/extract tasks want.
    gen_params: dict[str, Any] = field(
        default_factory=lambda: {"temperature": 0, "reasoning_effort": "minimal"}
    )
    # Consensus accept (optional): if `consensus_k` validated rungs agree on the
    # same `result[consensus_key]` value, accept it even if no single rung's
    # confidence reached τ. Cross-model agreement is often a stronger signal
    # than any one model's self-confidence. Disabled when either is None.
    consensus_k: Optional[int] = None
    consensus_key: Optional[str] = None


@dataclass
class Attempt:
    rung: Rung
    accepted: bool
    confidence: Optional[float] = None
    validated: bool = False
    validation_reason: str = ""
    result: Optional[dict[str, Any]] = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    error: Optional[str] = None


@dataclass
class CascadeResult:
    accepted: bool
    needs_human: bool
    result: Optional[dict[str, Any]]
    confidence: Optional[float]
    winning_rung: Optional[Rung]
    attempts: list[Attempt]
    accepted_via: Optional[str] = None   # "threshold" | "consensus" when accepted


async def run_cascade(
    task: StructuredTask,
    payload: Any,
    *,
    ladder: Optional[tuple[Rung, ...]] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
) -> CascadeResult:
    """Run `task` over the ladder, escalating until a rung is accepted.

    `transport_for(provider)` is injectable for testing; defaults to the
    transport registry. Returns the first accepted result, or — if every rung
    fails the gate — the best (highest-confidence) attempt with
    `needs_human=True`.
    """
    rungs = ladder or task.ladder or DEFAULT_LADDER
    get_transport = transport_for or transport_registry.get_transport
    user_text = task.build_user_message(payload)

    async def execute(rung: Rung):
        return await _complete_structured(
            get_transport(rung.provider), rung, task.system_prompt, user_text,
            gen_params=task.gen_params,
        )

    return await run_ladder(
        rungs, execute, task.validate, task.threshold, task.name,
        consensus_k=task.consensus_k, consensus_key=task.consensus_key,
    )


async def run_ladder(
    rungs: tuple[Rung, ...],
    execute: Callable[[Rung], Any],
    validate: Callable[[dict[str, Any]], tuple[bool, str]],
    threshold: float,
    name: str,
    consensus_k: Optional[int] = None,
    consensus_key: Optional[str] = None,
) -> CascadeResult:
    """Generic cheapest-first escalation, shared by the single-shot cascade and
    the agent-loop cascade.

    `execute(rung)` runs the rung and returns
    `(parsed_result, confidence, usage, error)`. Each result runs the
    validation+confidence gate; the first accepted rung wins; if none pass, the
    best attempt is returned with `needs_human=True` (never auto-accept a
    failing result).

    Optional consensus: when `consensus_k`/`consensus_key` are set, a rung is
    also accepted if `consensus_k` validated rungs (including this one) agree on
    the same `result[consensus_key]` value — even if no single rung reached τ.
    """
    attempts: list[Attempt] = []
    agree: dict[Any, int] = {}   # validated label -> count, for consensus
    for rung in rungs:
        t0 = time.perf_counter()
        parsed, confidence, usage, error = await execute(rung)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if error or parsed is None:
            attempts.append(Attempt(
                rung=rung, accepted=False, usage=usage, latency_ms=latency_ms,
                error=error or "no parseable result",
            ))
            logger.info(
                "cascade[%s] rung %s/%s -> escalate (error=%s, %dms)",
                name, rung.provider, rung.model, error, latency_ms,
            )
            continue

        validated, vreason = validate(parsed)
        accepted = validated and confidence is not None and confidence >= threshold
        accepted_via = "threshold" if accepted else None

        # Consensus: count agreement among validated rungs on consensus_key.
        if validated and consensus_k and consensus_key:
            label = parsed.get(consensus_key)
            if label is not None:
                agree[label] = agree.get(label, 0) + 1
                if not accepted and agree[label] >= consensus_k:
                    accepted = True
                    accepted_via = "consensus"

        attempts.append(Attempt(
            rung=rung, accepted=accepted, confidence=confidence,
            validated=validated, validation_reason=vreason, result=parsed,
            usage=usage, latency_ms=latency_ms,
        ))
        logger.info(
            "cascade[%s] rung %s/%s -> %s (validated=%s, conf=%s, τ=%.2f, %dms)",
            name, rung.provider, rung.model,
            (accepted_via.upper() if accepted_via else "escalate"),
            validated, confidence, threshold, latency_ms,
        )
        if accepted:
            return CascadeResult(
                accepted=True, needs_human=False, result=parsed,
                confidence=confidence, winning_rung=rung, attempts=attempts,
                accepted_via=accepted_via,
            )

    best = _best_attempt(attempts)
    logger.warning(
        "cascade[%s] EXHAUSTED %d rungs without acceptance -> needs_human",
        name, len(attempts),
    )
    return CascadeResult(
        accepted=False, needs_human=True,
        result=best.result if best else None,
        confidence=best.confidence if best else None,
        winning_rung=best.rung if best else None,
        attempts=attempts,
    )


def _best_attempt(attempts: list[Attempt]) -> Optional[Attempt]:
    """Prefer the highest-confidence validated attempt; else highest confidence."""
    scored = [a for a in attempts if a.confidence is not None]
    if not scored:
        return None
    validated = [a for a in scored if a.validated]
    pool = validated or scored
    return max(pool, key=lambda a: a.confidence or 0.0)


async def _complete_structured(
    transport: Any,
    rung: Rung,
    system: str,
    user_text: str,
    gen_params: Optional[dict[str, Any]] = None,
) -> tuple[Optional[dict[str, Any]], Optional[float], Usage, Optional[str]]:
    """One structured completion. Returns (parsed_result, confidence, usage, error)."""
    messages = [Message(role="user", content=[Text(text=user_text)])]
    text_parts: list[str] = []
    usage = Usage()
    error: Optional[str] = None
    try:
        async for ev in transport.stream(
            messages, model=rung.model, system=system,
            max_tokens=rung.max_tokens, tools=None, extra_body=gen_params,
        ):
            if isinstance(ev, TextDelta):
                text_parts.append(ev.text)
            elif isinstance(ev, Done):
                usage = ev.usage
            elif isinstance(ev, TransportError):
                error = f"{ev.code}: {ev.message}" if ev.code else ev.message
    except Exception as exc:  # transport blew up mid-stream
        error = f"{type(exc).__name__}: {exc}"

    if error:
        return None, None, usage, error

    parsed = _parse_json_object("".join(text_parts))
    if parsed is None:
        return None, None, usage, None
    confidence = _coerce_confidence(parsed.get("confidence"))
    return parsed, confidence, usage, None


def _parse_json_object(text: str) -> Optional[dict[str, Any]]:
    """Extract the first JSON object from model text (tolerant of ``` fences)."""
    s = text.strip()
    if s.startswith("```"):
        # strip a leading ```json / ``` fence and trailing ```
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.lstrip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip().rstrip("`").strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # Fallback: slice from the first { to the last }.
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            obj = json.loads(s[i:j + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _coerce_confidence(value: Any) -> Optional[float]:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, c))
