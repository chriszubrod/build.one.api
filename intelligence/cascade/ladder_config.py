"""Per-task cascade ladder and confidence threshold resolution.

Settings override code defaults:
  CASCADE_LADDER_<TASK>  — comma-separated rungs, e.g.
      "anthropic:claude-haiku-5-5@0.70,foundry!:DeepSeek-V4-Flash"
    A trailing ``!`` on the provider marks ``fallback_only`` (Foundry cash fallback).
    An optional ``@<float>`` suffix on a token sets that rung's acceptance τ in [0, 1].
  CASCADE_THRESHOLD_<TASK> — float in [0, 1]

Invalid values log one warning and fall back to the supplied default.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from config import Settings
from intelligence.cascade.core import Rung

logger = logging.getLogger(__name__)

# Live defaults (U-676f): Claude promotional credit first; Foundry is fallback-only.
DEFAULT_BILL_CODING_SUGGESTION_LADDER: tuple[Rung, ...] = (
    Rung("anthropic", "claude-haiku-5-5"),
    Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True),
    Rung("foundry", "gpt-5.4-nano", fallback_only=True),
    Rung("foundry", "gpt-5.4-mini", fallback_only=True),
)
DEFAULT_BILL_CODING_SUGGESTION_THRESHOLD = 0.5

DEFAULT_BILL_CODING_LADDER_V2: tuple[Rung, ...] = (
    Rung("anthropic", "claude-haiku-5-5", accept_threshold=0.70),
    Rung("anthropic", "claude-sonnet-5-5", accept_threshold=0.0),
    Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True),
    Rung("foundry", "gpt-5.4-nano", fallback_only=True),
    Rung("foundry", "gpt-5.4-mini", fallback_only=True),
)

DEFAULT_BILL_HEADER_EXTRACTION_LADDER: tuple[Rung, ...] = (
    Rung("anthropic", "claude-haiku-5-5"),
    Rung("anthropic", "claude-sonnet-5-5"),
    Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True),
    Rung("foundry", "gpt-5.4-nano", fallback_only=True),
    Rung("foundry", "gpt-5.4-mini", fallback_only=True),
)
DEFAULT_BILL_HEADER_EXTRACTION_THRESHOLD = 0.4

_warned_ladder: set[tuple[str, str]] = set()
_logged_ladder: set[tuple[str, str]] = set()
_warned_threshold: set[tuple[str, str]] = set()


def _settings_key_ladder(task_name: str) -> str:
    return f"cascade_ladder_{task_name}"


def _settings_key_threshold(task_name: str) -> str:
    return f"cascade_threshold_{task_name}"


def _parse_rung_threshold(raw: str) -> Optional[float]:
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(t) or t < 0 or t > 1:
        return None
    return t


def _format_rung_for_log(rung: Rung) -> str:
    provider = rung.provider + ("!" if rung.fallback_only else "")
    token = f"{provider}:{rung.model}"
    if rung.accept_threshold is not None:
        token = f"{token}@{rung.accept_threshold:g}"
    return token


def drop_ladder(ladder: tuple[Rung, ...]) -> tuple[Rung, ...]:
    """First non-fallback rung plus all fallback_only rungs (order preserved)."""
    if not ladder:
        return ()
    first_idx = next(
        (i for i, r in enumerate(ladder) if not r.fallback_only),
        None,
    )
    if first_idx is None:
        return ladder
    return (ladder[first_idx],) + tuple(r for r in ladder if r.fallback_only)


def escalate_ladder(ladder: tuple[Rung, ...]) -> tuple[Rung, ...]:
    """Non-fallback rungs after the first (order preserved)."""
    non_fallback = [r for r in ladder if not r.fallback_only]
    if len(non_fallback) <= 1:
        return ()
    return tuple(non_fallback[1:])


def _parse_ladder_spec(raw: str) -> Optional[tuple[Rung, ...]]:
    """Parse ``provider:model[@τ]`` tokens; ``provider!`` marks fallback_only."""
    rungs: list[Rung] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" not in token:
            return None
        provider_part, model_part = token.split(":", 1)
        provider_part = provider_part.strip()
        model_part = model_part.strip()
        if not provider_part or not model_part:
            return None
        accept_threshold: Optional[float] = None
        if "@" in model_part:
            model, thresh_raw = model_part.rsplit("@", 1)
            model = model.strip()
            if not model:
                return None
            accept_threshold = _parse_rung_threshold(thresh_raw.strip())
            if accept_threshold is None and thresh_raw.strip():
                return None
            # Empty suffix after @ is invalid (model@ with no value).
            if not thresh_raw.strip():
                return None
        else:
            model = model_part
        fallback_only = provider_part.endswith("!")
        provider = provider_part[:-1] if fallback_only else provider_part
        if provider not in ("anthropic", "foundry"):
            return None
        rungs.append(
            Rung(
                provider,
                model,
                fallback_only=fallback_only,
                accept_threshold=accept_threshold,
            )
        )
    return tuple(rungs) if rungs else None


def resolve_ladder(task_name: str, default: tuple[Rung, ...]) -> tuple[Rung, ...]:
    settings = Settings()
    key = _settings_key_ladder(task_name)
    raw = getattr(settings, key, None)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if not isinstance(raw, str):
        warn_key = (task_name, "<non-string>")
        if warn_key not in _warned_ladder:
            _warned_ladder.add(warn_key)
            logger.warning(
                "invalid CASCADE_LADDER_%s (not a string); using default",
                task_name.upper(),
            )
        return default
    parsed = _parse_ladder_spec(raw)
    if parsed is None:
        warn_key = (task_name, raw)
        if warn_key not in _warned_ladder:
            _warned_ladder.add(warn_key)
            logger.warning(
                "invalid CASCADE_LADDER_%s=%r; using default",
                task_name.upper(),
                raw,
            )
        return default
    log_key = (task_name, raw)
    if log_key not in _logged_ladder:
        _logged_ladder.add(log_key)
        logger.info(
            "CASCADE_LADDER_%s resolved: %s",
            task_name.upper(),
            ", ".join(_format_rung_for_log(r) for r in parsed),
        )
    return parsed


def resolve_threshold(task_name: str, default: float) -> float:
    settings = Settings()
    key = _settings_key_threshold(task_name)
    raw = getattr(settings, key, None)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        raw = str(raw)
    try:
        t = float(raw.strip())
    except (TypeError, ValueError):
        warn_key = (task_name, str(raw))
        if warn_key not in _warned_threshold:
            _warned_threshold.add(warn_key)
            logger.warning(
                "invalid CASCADE_THRESHOLD_%s=%r; using default",
                task_name.upper(),
                raw,
            )
        return default
    if not math.isfinite(t) or t < 0 or t > 1:
        warn_key = (task_name, str(raw))
        if warn_key not in _warned_threshold:
            _warned_threshold.add(warn_key)
            logger.warning(
                "invalid CASCADE_THRESHOLD_%s=%r (out of range); using default",
                task_name.upper(),
                raw,
            )
        return default
    return t
