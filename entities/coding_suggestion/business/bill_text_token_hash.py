"""Keyed HMAC token hashes for bill PDF text (U-701a)."""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

from entities.coding_suggestion.business.similar_bills import tokenize_bill_text

logger = logging.getLogger(__name__)

TOKENIZER_BASE = "u701a-v1"
_HASH_TRUNC_BYTES = 8
_MIN_KEY_LEN = 32

_warned_missing_key = False
_warned_short_key = False


def bill_text_token_hash_key(settings: Optional[object] = None) -> Optional[str]:
    if settings is None:
        import config

        settings = config.Settings()
    key = getattr(settings, "bill_text_token_hash_key", None)
    if key is None or not str(key).strip():
        return None
    return str(key)


def key_fingerprint(key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), b"fp", hashlib.sha256).digest()
    return digest[:4].hex()


def tokenizer_version_for_key(key: str) -> str:
    return f"{TOKENIZER_BASE}:{key_fingerprint(key)}"


def hash_token(token: str, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).digest()
    return digest[:_HASH_TRUNC_BYTES].hex()


def hash_token_int(token: str, key: str) -> int:
    return int(hash_token(token, key), 16)


def token_hashes_from_text(text: str, key: str) -> frozenset[str]:
    return frozenset(hash_token(t, key) for t in tokenize_bill_text(text))


def token_hashes_from_text_int(text: str, key: str) -> frozenset[int]:
    return frozenset(hash_token_int(t, key) for t in tokenize_bill_text(text))


def token_hashes_to_storage(tokens: frozenset[str]) -> tuple[str, int]:
    ordered = sorted(tokens)
    return " ".join(ordered), len(ordered)


def token_hashes_from_storage(stored: str) -> frozenset[str]:
    if not stored or not stored.strip():
        return frozenset()
    return frozenset(stored.split())


def token_hashes_from_storage_int(stored: str) -> frozenset[int]:
    if not stored or not stored.strip():
        return frozenset()
    return frozenset(int(part, 16) for part in stored.split())



def feature_enabled(settings: Optional[object] = None) -> bool:
    key = bill_text_token_hash_key(settings)
    if key is None:
        return False
    return len(key) >= _MIN_KEY_LEN


def current_tokenizer_version(settings: Optional[object] = None) -> Optional[str]:
    key = bill_text_token_hash_key(settings)
    if key is None or len(key) < _MIN_KEY_LEN:
        return None
    return tokenizer_version_for_key(key)


def warn_once_if_disabled() -> None:
    global _warned_missing_key, _warned_short_key
    key = bill_text_token_hash_key()
    if key is None or not str(key).strip():
        if not _warned_missing_key:
            _warned_missing_key = True
            logger.warning("similar_bills.token_hash_disabled reason=missing_bill_text_token_hash_key")
        return
    if len(key) < _MIN_KEY_LEN:
        if not _warned_short_key:
            _warned_short_key = True
            logger.warning(
                "similar_bills.token_hash_disabled reason=key_too_short min_len=%s",
                _MIN_KEY_LEN,
            )
