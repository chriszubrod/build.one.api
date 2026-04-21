# Python Standard Library Imports
import hashlib
import hmac
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)


def _get_secret() -> bytes:
    secret = os.getenv("OAUTH_STATE_SECRET")
    if not secret:
        raise RuntimeError(
            "OAUTH_STATE_SECRET environment variable is not set. "
            "Required for signing OAuth state tokens used in CSRF defense."
        )
    return secret.encode("utf-8")


def create_state() -> str:
    """
    Create a signed state token for OAuth CSRF protection.

    Returns a string of the form `<nonce>.<timestamp>.<hmac_hex>` that encodes
    a fresh nonce and the current UTC timestamp, signed with HMAC-SHA256.
    The callback handler verifies the signature and expiry before trusting
    the returned state.
    """
    nonce = secrets.token_urlsafe(16)
    ts = str(int(time.time()))
    payload = f"{nonce}.{ts}"
    sig = hmac.new(_get_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_state(state: str, max_age_seconds: int = 600) -> bool:
    """
    Verify a signed state token produced by `create_state`.

    Returns True iff the signature is valid AND the token is less than
    `max_age_seconds` old. Constant-time comparison is used to avoid
    timing oracles.
    """
    if not state:
        return False
    parts = state.split(".")
    if len(parts) != 3:
        return False
    nonce, ts_str, sig = parts
    expected = hmac.new(_get_secret(), f"{nonce}.{ts_str}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    return (int(time.time()) - ts) <= max_age_seconds
