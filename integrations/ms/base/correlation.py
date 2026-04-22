# Python Standard Library Imports
import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional


_correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ms_correlation_id", default=None
)

# Idempotency key propagation for outbox-driven writes. When the MS outbox
# worker is dispatching a retryable operation, it sets this context to the
# row's stable RequestId. MsGraphClient falls back to this value when no
# explicit idempotency_key is passed to write methods, which lets the worker
# drive idempotent writes without threading the key through every connector.
_idempotency_key_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ms_idempotency_key", default=None
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
    Used by `MsGraphClient` as a fallback when no explicit key is supplied.
    """
    return _idempotency_key_var.get()


@contextmanager
def idempotency_key_context(key: str) -> Iterator[None]:
    """
    Scope an idempotency key to the current execution context.

    Every write made through `MsGraphClient` within this block will use
    `key` as the `x-ms-client-request-id` header value unless the caller
    explicitly passes a different one. On exit the previous context value
    (usually None) is restored.

    Typical use by the outbox worker:

        with idempotency_key_context(row.request_id):
            connector.sync_upload(...)

    Retries of the same outbox row reuse the same key, which lets Graph
    deduplicate on its side.
    """
    token = _idempotency_key_var.set(key)
    try:
        yield
    finally:
        _idempotency_key_var.reset(token)
