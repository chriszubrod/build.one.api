"""Extraction JSON envelope shape and freshness (leaf module, U-701a)."""
from __future__ import annotations

from typing import Any

REDACTION_VERSION = "u535g-v3"

EXTRACTION_BLOB_KEYS = frozenset({
    "attachment_public_id",
    "source_file_hash",
    "redaction_version",
    "method",
    "pages_count",
    "content",
    "key_value_pairs",
})
_EXTRACTION_BLOB_OPTIONAL_KEYS = frozenset({"masking"})
ALLOWED_EXTRACTION_BLOB_KEYS = EXTRACTION_BLOB_KEYS | _EXTRACTION_BLOB_OPTIONAL_KEYS
EXTRACTION_KVP_KEYS = frozenset({"key", "value", "confidence"})


def extraction_payload_is_current(payload: object) -> bool:
    """True only for an extraction blob the current writer could have produced."""
    if not (
        isinstance(payload, dict)
        and payload.get("redaction_version") == REDACTION_VERSION
        and set(payload) <= ALLOWED_EXTRACTION_BLOB_KEYS
        and isinstance(payload.get("content", ""), str)
    ):
        return False
    kvps = payload.get("key_value_pairs", [])
    return isinstance(kvps, list) and all(
        isinstance(kvp, dict)
        and set(kvp) <= EXTRACTION_KVP_KEYS
        and all(kvp.get(f) is None or isinstance(kvp.get(f), str) for f in ("key", "value"))
        for kvp in kvps
    )
