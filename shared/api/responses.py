# Python Standard Library Imports
import logging
from typing import Any, Optional
from uuid import UUID

# Third-party Imports
from fastapi import HTTPException, status

# Local Imports
from shared.access import EntityNotAccessibleError
from shared.api.errors import ApiError, ErrorCode
from shared.db_constraints import (
    FK_MISSING_MESSAGE,
    FK_REFERENCE_MESSAGE,
    UNIQUE,
    looks_like_unique_violation,
    status_for_clean_message,
)
from shared.database import DatabaseConstraintError

logger = logging.getLogger(__name__)


def list_response(data: list[dict], count: Optional[int] = None) -> dict:
    """Standard envelope for list endpoints."""
    return {
        "data": data,
        "count": count if count is not None else len(data),
    }


def item_response(data: Optional[dict]) -> dict:
    """Standard envelope for single-entity endpoints."""
    return {"data": data}


def accepted_response(id: str, id_field: str = "id") -> dict:
    """Standard envelope for 202 ACCEPTED (async operations)."""
    return {"status": "accepted", id_field: id}


# Lifecycle rejections reach this helper as flattened strings (ProcessEngine folds a
# service exception into `"error": str(e)`), so Phase 0 derives the code from the
# phrase the SERVER emits. Keyed on emitted phrases only — the update/delete guards
# in entities/time_entry/business/service.py and time_log_service.py say
# "Only 'draft' entries can be …"; submit says "Cannot transition from … to …".
# Bridge (U-357d) — retire in LS-01 once typed lifecycle exceptions carry the code
# from where the rule is decided (`shared/lifecycle/assert_transition`).
_WORKFLOW_ERROR_CODES: tuple[tuple[str, str], ...] = (
    ("transition", ErrorCode.TRANSITION_INVALID),
    ("only 'draft' entries", ErrorCode.ENTRY_LOCKED),
)


def _derive_workflow_error_code(err_lower: str) -> str | None:
    return next((code for phrase, code in _WORKFLOW_ERROR_CODES if phrase in err_lower), None)


# Business rules that deserve a status OTHER than the 400 default, matched on an
# exact message PREFIX rather than a loose substring. Same root cause as
# `_WORKFLOW_ERROR_CODES` above and `status_for_clean_message` in
# shared/db_constraints.py — ProcessEngine folds a service exception into
# `"error": str(e)`, so a prefix the service owns is the only structure that
# survives to the router. Prefixes live here, not in the entity, because the
# direction of the dependency has to be entity -> shared.
REVIEW_STATUS_SHAPE_PREFIX = "Review status configuration is invalid: "

_WORKFLOW_STATUS_BY_PREFIX: tuple[tuple[str, int, str], ...] = (
    (REVIEW_STATUS_SHAPE_PREFIX, status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.REVIEW_STATUS_SHAPE),
)


def _status_and_code_for_prefix(err: str) -> tuple[int, str] | None:
    """422 + code for a business-rule rejection the engine flattened to a string."""
    return next(
        ((st, code) for prefix, st, code in _WORKFLOW_STATUS_BY_PREFIX if err.startswith(prefix)),
        None,
    )


def raise_workflow_error(
    err: str,
    default_message: str,
    *,
    error_code: str | None = None,
) -> None:
    """Map workflow engine error strings to appropriate HTTP exceptions.

    Reusable across all routers that call ProcessEngine.execute_synchronous().
    An explicit `error_code` always wins; otherwise one is derived for the plain
    400 branch only (never for the constraint-clean or 409 branches).
    """
    if not err:
        status_code, detail = status.HTTP_400_BAD_REQUEST, default_message
    else:
        detail = err
        err_lower = err.lower()
        # Clean constraint messages must not be re-routed by the 'already exists' -> 409
        # rule, because 409 is the status the iOS client maps to its optimistic-concurrency
        # conflict flow (which discards the queued local edit); 422 keeps it in the
        # non-discarding requestFailed bucket.
        prefixed = _status_and_code_for_prefix(err)
        clean_status = status_for_clean_message(err)
        if prefixed is not None:
            # Checked FIRST: a shape rejection names rows and counts, so it can
            # contain "already exists" and would otherwise be re-routed to 409 —
            # the status iOS maps to its concurrency-conflict flow, which
            # DISCARDS the local edit.
            status_code, derived_code = prefixed
            if error_code is None:
                error_code = derived_code
        elif clean_status is not None:
            status_code = clean_status
        elif any(phrase in err_lower for phrase in ("already exists", "concurrency", "row-version")):
            status_code = status.HTTP_409_CONFLICT
        else:
            status_code = status.HTTP_400_BAD_REQUEST
            if error_code is None:
                error_code = _derive_workflow_error_code(err_lower)
    raise ApiError(status_code=status_code, detail=detail, error_code=error_code)


def raise_not_found(entity_name: str) -> None:
    """Raise a standard 404 for a missing entity."""
    raise ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{entity_name} not found",
        error_code=ErrorCode.NOT_FOUND,
    )


def classify_database_error(error: Exception) -> Optional[ApiError]:
    """Map a database-layer failure to its transport-correct status, or None when
    it is not a recognized constraint violation.

    Split out from `raise_database_error` so a caller can ASK whether an error is
    classifiable without catching a raise to find out (see `raise_server_error`).

    Unique-key violations surface as 422 with the ORIGINAL message — the
    iOS offline-sync client keys its duplicate-claim recovery off
    `.requestFailed` (any 4xx except 401/404/409) + a message containing
    'duplicate' / 'unique' / the constraint name. Deliberately NOT 409:
    the client maps 409 to its optimistic-concurrency conflict flow, which
    would bypass the claim logic entirely (round-2 review 2026-06-10).

    Foreign-key violations (SQL 547) also surface as 422, with a schema-free
    clean message. Also deliberately NOT 409: iOS reaches a 547 through
    FK_TimeLog_Project on POST /api/v1/time-entries/{id}/logs and PUT
    /api/v1/time-logs/{id} (TimeLogService never validates project_id, so a
    stale offline project id lands in the sproc), and its 409 branch DISCARDS
    the queued local edit — destroying a field worker's clock-out and note.
    422 keeps it in the terminal-but-non-discarding `.requestFailed` bucket,
    same as the unique-key branch.

    An error already classified by map_database_error arrives as a
    DatabaseConstraintError and is handled by the type-first branch below;
    shared/db_constraints.py owns that two-signal detection (error number +
    FK/UNIQUE phrase) so this handler cannot drift from the workflow path.

    The string branches below remain as the looser fallback for a message that
    reached us unclassified — notably one carrying no parenthesized error number
    at all, which db_constraints deliberately declines to classify.

    Anything else returns None.
    """
    if isinstance(error, DatabaseConstraintError):
        # U-154 contract preserved: unique-key violations on this path surface the
        # ORIGINAL driver message, because the iOS duplicate-claim matcher keys off
        # 'duplicate'/'unique'/the constraint name. FK violations surface the clean
        # schema-free message.
        is_unique = error.violation.kind == UNIQUE
        return ApiError(
            status_code=error.violation.http_status,
            detail=error.original if is_unique else error.violation.message,
            error_code=ErrorCode.DUPLICATE_KEY if is_unique else ErrorCode.FK_VIOLATION,
        )
    message = str(error)
    lower = message.lower()
    # Phrase test imported, not re-typed: a bare `"unique" in lower` also matched the
    # TYPE NAME in "Error converting data type nvarchar to uniqueidentifier" (SQL 8114),
    # turning a caller's malformed-UUID into a 422 duplicate_key echoing ODBC internals.
    if looks_like_unique_violation(message):
        return ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message,
            error_code=ErrorCode.DUPLICATE_KEY,
        )
    if "547" in message:
        # Same wording the classified path returns — imported, not re-typed, so a
        # reworded message can't silently stop matching status_for_clean_message()
        # on the workflow path.
        if "reference constraint" in lower:
            return ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=FK_REFERENCE_MESSAGE,
                error_code=ErrorCode.FK_VIOLATION,
            )
        if "foreign key constraint" in lower:
            return ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=FK_MISSING_MESSAGE,
                error_code=ErrorCode.FK_VIOLATION,
            )
    return None


def raise_database_error(error: Exception) -> None:
    """Raise the classified transport error for a database-layer failure, or
    re-raise the original unchanged when it is not a recognized violation."""
    api_error = classify_database_error(error)
    if api_error is not None:
        raise api_error
    raise error


def parse_public_id(value: Any, field_name: str) -> str:
    """Validate a public-id parameter before it reaches a UNIQUEIDENTIFIER bind.

    Every `by-<x>/{<x>_public_id}` route feeds its parameter into a sproc whose
    parameter is declared UNIQUEIDENTIFIER. A non-UUID gets that far and fails in
    the DRIVER (SQL 8114, "Error converting data type nvarchar to
    uniqueidentifier"), which the blanket `detail=str(e)` handlers then echo back
    to the caller verbatim -- ODBC internals, server-side type names and all.
    Rejecting the shape here keeps the failure a clean 422 in the same envelope
    every other validation error uses, and never opens a DB connection.

    Returns the canonical hyphenated form, so a braced or unhyphenated UUID
    binds the same as any other.
    """
    try:
        return str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must be a UUID",
            error_code=ErrorCode.VALIDATION_ERROR,
        ) from None


def raise_server_error(error: Exception, message: str) -> None:
    """Terminal handler for an unexpected exception escaping a router.

    A classified database failure keeps the transport-correct 4xx
    `classify_database_error` gives it. Anything else is logged with its traceback
    server-side and surfaces as a generic 500 carrying only `message`, so a bare
    `HTTPException(500, detail=str(e))` can no longer hand the caller driver and
    schema internals. (Not an unconditional promise: a unique violation still
    returns its ORIGINAL driver message by design -- the iOS duplicate-claim
    matcher keys on it. See classify_database_error.)

    Two exception types are deliberately let through to their own handlers,
    because a generic 500 would be WRONG, not merely coarse:
      - `EntityNotAccessibleError` must reach entity_not_accessible_handler, which
        answers 404 (never 403) so the URL does not confirm the entity exists to a
        caller without UserProject access.
      - Any `HTTPException` a service already chose (including every ApiError from
        the raise_* helpers) is a deliberate status, not an accident.
    """
    if isinstance(error, (EntityNotAccessibleError, HTTPException)):
        raise error
    api_error = classify_database_error(error)
    if api_error is not None:
        raise api_error
    # exc_info=error, not logger.exception: this helper is also called outside an
    # `except` block, where ambient sys.exc_info() would log "NoneType: None".
    logger.error(message, exc_info=error)
    raise ApiError(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)
