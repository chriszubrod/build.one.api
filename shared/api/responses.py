# Python Standard Library Imports
from typing import Any, Optional

# Third-party Imports
from fastapi import HTTPException, status

# Local Imports
from shared.db_constraints import (
    FK_MISSING_MESSAGE,
    FK_REFERENCE_MESSAGE,
    UNIQUE,
    status_for_clean_message,
)
from shared.database import DatabaseConstraintError


def list_response(data: list[dict], count: Optional[int] = None) -> dict:
    """Standard envelope for list endpoints."""
    return {
        "data": data,
        "count": count if count is not None else len(data),
    }


def item_response(data: dict) -> dict:
    """Standard envelope for single-entity endpoints."""
    return {"data": data}


def accepted_response(id: str, id_field: str = "id") -> dict:
    """Standard envelope for 202 ACCEPTED (async operations)."""
    return {"status": "accepted", id_field: id}


def raise_workflow_error(err: str, default_message: str) -> None:
    """Map workflow engine error strings to appropriate HTTP exceptions.

    Reusable across all routers that call ProcessEngine.execute_synchronous().
    """
    if not err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=default_message,
        )
    # Clean constraint messages must not be re-routed by the 'already exists' -> 409
    # rule, because 409 is the status the iOS client maps to its optimistic-concurrency
    # conflict flow (which discards the queued local edit); 422 keeps it in the
    # non-discarding requestFailed bucket.
    status_code = status_for_clean_message(err)
    if status_code is not None:
        raise HTTPException(status_code=status_code, detail=err)
    err_lower = err.lower()
    if "already exists" in err_lower:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err)
    if "concurrency" in err_lower or "row-version" in err_lower:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err)
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)


def raise_not_found(entity_name: str) -> None:
    """Raise a standard 404 for a missing entity."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{entity_name} not found",
    )


def raise_database_error(error: Exception) -> None:
    """Map database-layer failures escaping a router to transport-correct
    statuses instead of opaque 500s.

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

    Anything else re-raises unchanged.
    """
    if isinstance(error, DatabaseConstraintError):
        # U-154 contract preserved: unique-key violations on this path surface the
        # ORIGINAL driver message, because the iOS duplicate-claim matcher keys off
        # 'duplicate'/'unique'/the constraint name. FK violations surface the clean
        # schema-free message.
        raise HTTPException(
            status_code=error.violation.http_status,
            detail=error.original if error.violation.kind == UNIQUE else error.violation.message,
        )
    message = str(error)
    lower = message.lower()
    if "duplicate key" in lower or "unique" in lower:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message,
        )
    if "547" in message:
        # Same wording the classified path returns — imported, not re-typed, so a
        # reworded message can't silently stop matching status_for_clean_message()
        # on the workflow path.
        if "reference constraint" in lower:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=FK_REFERENCE_MESSAGE,
            )
        if "foreign key constraint" in lower:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=FK_MISSING_MESSAGE,
            )
    raise error
