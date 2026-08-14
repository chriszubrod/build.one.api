"""
Shared auth-failure classification for retry vs dead-letter decisions (U-224).

Promoted out of the QBO-only home in integrations/intuit/qbo/base/errors.py (U-215)
so MS and other integrations can classify token-refresh failures without importing
a QBO module.

Also holds the shared ``classify_failure`` helper that logs and returns a
``(None, AuthFailureKind)`` tuple in one step.
"""
# Python Standard Library Imports
from enum import Enum


class AuthFailureKind(str, Enum):
    """
    Classification of token-refresh failures for retry vs dead-letter decisions.

    TRANSIENT = worth retrying (lock timeout, Intuit 5xx/429, network/DB blip).
    PERMANENT = only a human re-authorization fixes it (invalid_grant, no auth record).
    """

    NONE = "none"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


def classify_failure(
    logger: "logging.Logger",
    message: str,
    kind: "AuthFailureKind",
    *,
    exc_info: bool = False,
) -> tuple[None, "AuthFailureKind"]:
    """
    Log a refresh failure and return the `(None, kind)` classification in one step.

    The point is that the kind in the LOG and the kind in the RETURN can never disagree.
    That log line is the operator's only signal for why an outbox row retried instead of
    dead-lettering — a mismatch would send whoever is triaging in the wrong direction.

    `logger` is any object exposing `.error(msg)` / `.exception(msg)` — accepted as a parameter
    (not imported) so this stays integration-agnostic; each caller passes its own logger.
    """
    log_msg = f"{message} (failure_kind={kind.value})"
    if exc_info:
        logger.exception(log_msg)
    else:
        logger.error(log_msg)
    return None, kind
