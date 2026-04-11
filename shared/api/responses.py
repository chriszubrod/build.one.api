# Python Standard Library Imports
from typing import Any, Optional

# Third-party Imports
from fastapi import HTTPException, status


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
