"""Structured telemetry for cascade rung outcomes.

The cascade emits one INFO record per decision (a finished ladder, or a rung
transition in the transport). Each record's `extra` fields become App Insights
customDimensions, so win rate, cost and latency can be queried per rung.

INFO export to App Insights is decided by EXPORTED_INFO_LOGGERS in
shared/observability.py, which includes these loggers.

Telemetry never breaks a cascade call: enrichment and logging failures are
swallowed and degrade to a minimal event.
"""
import json
import logging
from decimal import Decimal
from typing import Any, Optional

from intelligence.observability.pricing import compute_cost_usd

LADDER_EVENT = "cascade.ladder.completed"
TRANSPORT_EVENT = "cascade.transport.rung"


def flat_extra(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop None values: OpenTelemetry rejects them and warns, which exports a noise trace."""
    return {k: v for k, v in fields.items() if v is not None}


def log_safely(
    logger: logging.Logger, level: int, msg: str, *args: Any, stacklevel: int = 2, **kwargs: Any,
) -> None:
    """Log, swallowing any logging error. `stacklevel` defaults to 2 so the record
    names the caller of log_safely, not this helper, as its source location."""
    try:
        logger.log(level, msg, *args, stacklevel=stacklevel, **kwargs)
    except Exception:
        pass


def log_transport_event(logger: logging.Logger, **fields: Any) -> None:
    log_safely(
        logger, logging.INFO, TRANSPORT_EVENT,
        extra=flat_extra({**fields, "event_name": TRANSPORT_EVENT}),
        stacklevel=3,  # name the caller of log_transport_event
    )


def log_ladder_outcome(logger: logging.Logger, name: str, result: Any) -> None:
    try:
        if not logger.isEnabledFor(logging.INFO):
            return
        try:
            fields = ladder_event_fields(name, result)
        except Exception as exc:
            fields = {
                "event_name": LADDER_EVENT,
                "task": name,
                "accepted": _safe_attr(result, "accepted"),
                "needs_human": _safe_attr(result, "needs_human"),
                "telemetry_error": type(exc).__name__,
            }
        logger.info(LADDER_EVENT, extra=flat_extra(fields), stacklevel=2)  # name run_ladder, not this helper
    except Exception:
        pass


def ladder_event_fields(name: str, result: Any) -> dict[str, Any]:
    attempts = result.attempts
    winner = result.winning_rung
    costs = [_attempt_cost(a) for a in attempts]
    cost_usd = None if any(c is None for c in costs) else round(sum(costs), 6)
    return flat_extra({
        "event_name": LADDER_EVENT,
        "task": name,
        "accepted": result.accepted,
        "needs_human": result.needs_human,
        "accepted_via": result.accepted_via,
        "winning_provider": winner.provider if winner else None,
        "winning_model": winner.model if winner else None,
        "winning_index": getattr(result, "winning_index", None),  # set by run_ladder
        "attempt_count": len(attempts),
        "latency_ms_total": sum(a.latency_ms for a in attempts),
        "input_tokens": _sum_usage(attempts, "input_tokens"),
        "output_tokens": _sum_usage(attempts, "output_tokens"),
        "cost_usd": cost_usd,
        "attempts_json": json.dumps([_attempt_json(a) for a in attempts], default=str),
    })


def _safe_attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _attempt_cost(attempt: Any) -> Optional[float]:
    if attempt.usage is None or attempt.rung is None:
        return None
    try:
        return compute_cost_usd(provider=attempt.rung.provider, model=attempt.rung.model, usage=attempt.usage)
    except Exception:
        return None


def _sum_usage(attempts: list, field: str) -> int:
    return sum(getattr(a.usage, field) for a in attempts if a.usage is not None)


def cascade_usage_totals(
    result: Any,
) -> tuple[int, int, Optional[Decimal]]:
    """Sum tokens and USD cost for one cascade (None cost if any attempt price unknown)."""
    if result is None:
        return 0, 0, None
    attempts = result.attempts
    costs = [_attempt_cost(a) for a in attempts]
    if any(c is None for c in costs):
        cost_usd: Optional[Decimal] = None
    elif not costs:
        cost_usd = Decimal("0")
    else:
        cost_usd = Decimal(str(round(sum(costs), 6)))
    return (
        _sum_usage(attempts, "input_tokens"),
        _sum_usage(attempts, "output_tokens"),
        cost_usd,
    )


def _attempt_json(attempt: Any) -> dict[str, Any]:
    rung = attempt.rung
    return {
        "provider": rung.provider if rung is not None else None,
        "model": rung.model if rung is not None else None,
        "accepted": attempt.accepted,
        "validated": attempt.validated,
        "confidence": _confidence(attempt.confidence),
        "latency_ms": attempt.latency_ms,
        "error": None if attempt.error is None else str(attempt.error),
    }


def _confidence(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return str(value)
