# Python Standard Library Imports
import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional


_correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "box_correlation_id", default=None
)

# Idempotency key propagation for outbox-driven writes. When the Box outbox
# worker (Phase 2) is dispatching a retryable operation, it sets this context
# to the row's stable RequestId. Box has no Graph-style client-request-id
# dedup header, so the key is never sent on the wire — it identifies the
# outbox row so retries of the same row are recognizable in logs and so
# row-level handlers can implement their own dedup (e.g., pre-flight lookup).
_idempotency_key_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "box_idempotency_key", default=None
)


def get_correlation_id() -> Optional[str]:
    """Return the correlation ID for the current execution context, if any."""
    return _correlation_id_var.get()


def set_correlation_id(correlation_id: str) -> contextvars.Token:
    """
    Set the correlation ID for the current execution context. Returns a
    Token that can be passed to `reset_correlation_id` to restore the
    previous value, mirroring ContextVar semantics.
    """
    return _correlation_id_var.set(correlation_id)


def reset_correlation_id(token: contextvars.Token) -> None:
    """Restore the correlation ID to its value before the matching `set` call."""
    _correlation_id_var.reset(token)


def ensure_correlation_id() -> str:
    """
    Return the current correlation ID, or generate+install a fresh one
    if none is set. Used by the shared HTTP client so every outbound call
    has a correlation ID even when no upstream middleware set one.
    """
    existing = _correlation_id_var.get()
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    _correlation_id_var.set(new_id)
    return new_id


def get_idempotency_key() -> Optional[str]:
    """
    Return the idempotency key for the current execution context, if any.
    Consumed by the Phase 2 outbox worker's per-row handlers — the shared
    BoxHttpClient does NOT inject it as a header (Box has no dedup header).
    """
    return _idempotency_key_var.get()


@contextmanager
def idempotency_key_context(key: str) -> Iterator[None]:
    """
    Scope an idempotency key to the current execution context.

    Typical use by the outbox worker:

        with idempotency_key_context(row.request_id):
            connector.sync_upload(...)

    Retries of the same outbox row reuse the same key. Unlike MS Graph,
    Box offers no client-request-id dedup header, so the key is used for
    log correlation and handler-level dedup only — never sent to Box.
    On exit the previous context value (usually None) is restored.
    """
    token = _idempotency_key_var.set(key)
    try:
        yield
    finally:
        _idempotency_key_var.reset(token)
