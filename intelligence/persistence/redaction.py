"""Mask taxpayer identifiers in agent history text before SQL persistence.

Reuses ``mask_tins`` from attachment extraction (U-535g). Pure — no I/O, no logging.
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any, Optional

from entities.attachment.business.tin_redaction import mask_tins

# Normalized dict keys that carry a bare or labeled TIN (W-9 / approval / tool payloads).
# ``taxpayer_id`` is intentionally omitted — it is an integer FK (e.g. Vendor.taxpayer_id).
_TIN_KEY_NAMES = frozenset(
    {
        "tin",  # generic tool/API field name
        "ssn",  # Social Security Number
        "ein",  # Employer Identification Number (EIN)
        "taxid",  # shorthand tax identifier
        "taxidnumber",
        "taxpayeridnumber",
        "taxpayeridentificationnumber",  # IRS phrasing
        "socialsecuritynumber",
        "employeridentificationnumber",
        "fein",  # Federal EIN
        "itin",  # Individual Taxpayer Identification Number
    }
)


def _normalize_key(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "").replace(" ", "")


def _is_tin_key(key: str) -> bool:
    nk = _normalize_key(key)
    return nk in _TIN_KEY_NAMES or nk.endswith("taxpayeridnumber")


def _mask_bare_numeric(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        digits = str(abs(value))
    else:
        digits = "".join(c for c in str(value) if c.isdigit())
    return "*" * len(digits) if digits else str(value)


def _mask_scalar_under_tin(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        masked, _ = mask_tins(value, aggressive=True)
        return masked
    if isinstance(value, (int, float)):
        return _mask_bare_numeric(value)
    return value


# Under a TIN-named container, a key is treated as a possible bare TIN only when it holds
# at least this many numeric characters; ordinary field names ("last4", "value1") keep
# the SSN/EIN-shape rule so the payload's schema is not mangled.
_MIN_DIGITS_FOR_ID_KEY = 7


def _mask_string_dict_key(key: str, *, under_tin_key: bool) -> str:
    aggressive = False
    if under_tin_key:
        # Fail closed like mask_tins(aggressive=True): a character this interpreter cannot
        # classify (Cn: e.g. a digit from a newer Unicode version) or a lone surrogate (Cs)
        # counts as a digit.
        looks_like_id = sum(
            ch.isnumeric() or unicodedata.category(ch) in ("Cn", "Cs") for ch in key
        ) >= _MIN_DIGITS_FOR_ID_KEY
        aggressive = looks_like_id
    masked, _ = mask_tins(key, aggressive=aggressive)
    return masked


def _collision_suffix(n: int) -> str:
    """Letters only ("b", "c", ..., "z", "aa", ...), so a suffix holds no digit that a
    later masking pass could change — re-masking is stable. n=2 -> "b"."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("a") + rem) + letters
    return letters


def _mask_json_value(value: Any, *, under_tin_key: bool = False) -> Any:
    if isinstance(value, dict):
        # Keys inside a TIN-named container that look like an id are masked aggressively
        # (a bare 9-digit TIN used as a key); every other key keeps the SSN/EIN-shape rule.
        masked_keys = {
            k: (_mask_string_dict_key(k, under_tin_key=under_tin_key) if isinstance(k, str) else k)
            for k in value
        }
        natural: set[Any] | None = None
        out: dict[Any, Any] = {}
        for k, v in value.items():
            out_key: Any = masked_keys[k]
            if out_key in out:
                if natural is None:
                    natural = set(masked_keys.values())
                # Two keys masked to the same text: never drop a member. Pick the first
                # "#b", "#c", ... suffix not already used and not another key's own masked name.
                n = 2
                candidate = f"{out_key}#{_collision_suffix(n)}"
                while candidate in out or candidate in natural:
                    n += 1
                    candidate = f"{out_key}#{_collision_suffix(n)}"
                out_key = candidate
            branch_under_tin = under_tin_key or (
                isinstance(k, str) and _is_tin_key(k)
            )
            out[out_key] = _mask_json_value(v, under_tin_key=branch_under_tin)
        return out
    if isinstance(value, list):
        return [_mask_json_value(item, under_tin_key=under_tin_key) for item in value]
    if under_tin_key:
        return _mask_scalar_under_tin(value)
    if isinstance(value, str):
        if len(value) < 10:
            return value
        masked, _ = mask_tins(value, aggressive=False)
        return masked
    return value


def mask_agent_json(value: Any) -> str:
    """Mask a decoded JSON value and re-serialize with default ``json.dumps`` settings."""
    return json.dumps(_mask_json_value(value))


def mask_agent_free_text(text: Optional[str]) -> Optional[str]:
    """Mask TINs in free text (user messages, model text, error text, summaries).

    Never parsed as JSON, so text that happens to be valid JSON is not re-serialized
    ("1e5" stays "1e5", whitespace and non-ASCII characters are untouched)."""
    if text is None or len(text) < 10:
        return text
    masked, _ = mask_tins(text, aggressive=False)
    return masked


def mask_agent_text(text: Optional[str]) -> Optional[str]:
    """Mask TINs in a JSON-serialized tool payload (ToolInput / ToolOutput).

    Not valid JSON -> masked as free text. Nesting too deep to parse or walk, or a
    number over Python's int-digit limit, fails CLOSED (every numeric character
    masked) instead of failing the history write."""
    if text is None:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return mask_agent_free_text(text)
    except (RecursionError, ValueError):  # ValueError: int exceeds the str-digit limit
        return mask_tins(text, aggressive=True)[0]
    try:
        return mask_agent_json(decoded)
    except (RecursionError, ValueError):
        return mask_tins(text, aggressive=True)[0]
