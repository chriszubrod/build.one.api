# Python Standard Library Imports
import contextvars
import uuid
from typing import Optional


_correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ramp_correlation_id", default=None
)


def get_correlation_id() -> Optional[str]:
    return _correlation_id_var.get()


def ensure_correlation_id() -> str:
    existing = _correlation_id_var.get()
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    _correlation_id_var.set(new_id)
    return new_id
