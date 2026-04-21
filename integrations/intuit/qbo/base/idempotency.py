# Python Standard Library Imports
import uuid
from typing import Optional


def generate_idempotency_key() -> str:
    """
    Generate a fresh UUID-v4 idempotency key.

    Returned as a canonical-form string (hex with hyphens) so it drops
    straight into a QBO `?requestid=<key>` query parameter without
    further encoding.
    """
    return str(uuid.uuid4())


def resolve_idempotency_key(caller_supplied: Optional[str]) -> str:
    """
    Return the caller's idempotency key if supplied; otherwise generate
    a fresh one.

    Used by the shared QboHttpClient as the single entry point for every
    write. One-shot callers don't need to pass anything — they get a fresh
    key each call. The outbox worker passes the stored key on every retry
    attempt for the same row, so QBO deduplicates duplicate POSTs that
    result from timeouts or process crashes.
    """
    if caller_supplied:
        return caller_supplied
    return generate_idempotency_key()


def is_valid_idempotency_key(key: str) -> bool:
    """
    True iff `key` parses as a valid UUID.

    Defensive check used by the outbox worker before sending a stored
    key: if an operator hand-edited an outbox row and corrupted the
    RequestId, we surface the problem locally instead of sending garbage
    to QBO and hoping the server rejects it cleanly.
    """
    try:
        uuid.UUID(key)
        return True
    except (ValueError, AttributeError, TypeError):
        return False
