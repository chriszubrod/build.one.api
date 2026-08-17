"""
Shared environment-variable boolean gates and positive-int readers.

Default-deny truthy checks and clamped int parsing used across integrations
and scripts — single source of truth so gate semantics cannot drift.
"""
import logging
import os
from typing import Optional

_DEFAULT_LOGGER = logging.getLogger(__name__)


def is_truthy(value: Optional[str]) -> bool:
    """True only when value, stripped and lowercased, is exactly 'true'."""
    return (value or "").strip().lower() == "true"


def env_flag_enabled(name: str) -> bool:
    """Default-deny env-var boolean gate: os.environ[name] must literally be 'true' (case-insensitive)."""
    return is_truthy(os.environ.get(name))


def _env_positive_int(
    name: str, default: int, *, minimum: int, warn: bool = True, logger: Optional[logging.Logger] = None
) -> int:
    """
    Read an int env var, clamped to a floor. Missing/empty -> default silently.
    Unparseable or below `minimum` -> default, logging a warning iff warn=True:
    'Invalid {name}={raw!r}; using default {default}' for a parse failure;
    for a below-minimum value, 'Negative {name}={parsed}; using default {default}'
    when minimum<=0 (byte-identical to this helper's two original callers, both
    minimum=0), else 'Value {name}={parsed} below minimum {minimum}; using default {default}'.
    """
    log = logger or _DEFAULT_LOGGER
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
    except ValueError:
        if warn:
            log.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default
    if parsed < minimum:
        if warn:
            if minimum <= 0:
                log.warning("Negative %s=%s; using default %s", name, parsed, default)
            else:
                log.warning("Value %s=%s below minimum %s; using default %s", name, parsed, minimum, default)
        return default
    return parsed
