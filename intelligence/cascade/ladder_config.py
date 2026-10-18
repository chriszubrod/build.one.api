"""Per-task cascade ladder and confidence threshold resolution.

Settings override code defaults:
  CASCADE_LADDER_<TASK>  — comma-separated rungs, e.g.
      "anthropic:claude-haiku-5-5,foundry!:DeepSeek-V4-Flash"
    A trailing ``!`` on the provider marks ``fallback_only`` (Foundry cash fallback).
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

DEFAULT_BILL_HEADER_EXTRACTION_LADDER: tuple[Rung, ...] = (
    Rung("anthropic", "claude-haiku-5-5"),
    Rung("anthropic", "claude-sonnet-5-5"),
    Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True),
    Rung("foundry", "gpt-5.4-nano", fallback_only=True),
    Rung("foundry", "gpt-5.4-mini", fallback_only=True),
)
DEFAULT_BILL_HEADER_EXTRACTION_THRESHOLD = 0.4

_warned_ladder: set[tuple[str, str]] = set()
_warned_threshold: set[tuple[str, str]] = set()


def _settings_key_ladder(task_name: str) -> str:
    return f"cascade_ladder_{task_name}"


def _settings_key_threshold(task_name: str) -> str:
    return f"cascade_threshold_{task_name}"


def _parse_ladder_spec(raw: str) -> Optional[tuple[Rung, ...]]:
    """Parse ``provider:model`` tokens; ``provider!`` marks fallback_only."""
    rungs: list[Rung] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" not in token:
            return None
        provider_part, model = token.split(":", 1)
        provider_part = provider_part.strip()
        model = model.strip()
        if not provider_part or not model:
            return None
        fallback_only = provider_part.endswith("!")
        provider = provider_part[:-1] if fallback_only else provider_part
        if provider not in ("anthropic", "foundry"):
            return None
        rungs.append(Rung(provider, model, fallback_only=fallback_only))
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
