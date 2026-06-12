# Python Standard Library Imports
import uuid
from typing import Optional


def generate_idempotency_key() -> str:
    """
    Generate a fresh UUID-v4 idempotency key.

    Returned as a canonical-form string (hex with hyphens). Box has no
    Graph-style client-request-id dedup header, so the key is never sent
    on the wire — the Phase 2 outbox stores it as the row's stable
    RequestId for retry identity and log correlation.
    """
    return str(uuid.uuid4())


def resolve_idempotency_key(caller_supplied: Optional[str]) -> str:
    """
    Return the caller's idempotency key if supplied; otherwise generate
    a fresh one.

    One-shot callers don't need to pass anything — they get a fresh key
    each call. The outbox worker passes the stored key on every retry
    attempt for the same row, so duplicate effects from timeouts or
    process crashes are detectable at the handler level.
    """
    if caller_supplied:
        return caller_supplied
    return generate_idempotency_key()


def is_valid_idempotency_key(key: str) -> bool:
    """
    True iff `key` parses as a valid UUID.

    Defensive check used by the outbox worker before trusting a stored
    key: if an operator hand-edited an outbox row and corrupted the
    RequestId, we surface the problem locally instead of carrying a
    malformed key through the dispatch path.
    """
    try:
        uuid.UUID(key)
        return True
    except (ValueError, AttributeError, TypeError):
        return False
