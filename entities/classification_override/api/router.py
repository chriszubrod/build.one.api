# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.auth.business.service import get_current_user_api
from entities.classification_override.api.schemas import (
    ClassificationOverrideCreate,
    ClassificationOverrideUpdate,
)
from entities.classification_override.business.service import ClassificationOverrideService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "admin"])


@router.get("/classification-overrides")
def list_overrides(current_user: dict = Depends(get_current_user_api)):
    """List all classification overrides."""
    svc = ClassificationOverrideService()
    overrides = svc.read_all()
    return [o.to_dict() for o in overrides]


@router.get("/classification-overrides/{public_id}")
def get_override(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """Get a single classification override by public ID."""
    svc = ClassificationOverrideService()
    override = svc.read_by_public_id(public_id)
    if not override:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Override not found: {public_id}",
        )
    return override.to_dict()


@router.post("/classification-overrides", status_code=status.HTTP_201_CREATED)
def create_override(
    body: ClassificationOverrideCreate,
    current_user: dict = Depends(get_current_user_api),
):
    """Create a new classification override."""
    svc = ClassificationOverrideService()
    try:
        override = svc.create(
            match_type=body.match_type,
            match_value=body.match_value,
            classification_type=body.classification_type,
            notes=body.notes,
            is_active=body.is_active,
            created_by=current_user.get("username"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An override for {body.match_type} '{body.match_value}' already exists.",
            )
        raise
    return override.to_dict()


@router.put("/classification-overrides/{public_id}")
def update_override(
    public_id: str,
    body: ClassificationOverrideUpdate,
    current_user: dict = Depends(get_current_user_api),
):
    """Update a classification override."""
    svc = ClassificationOverrideService()
    try:
        override = svc.update(
            public_id=public_id,
            row_version=body.row_version,
            match_type=body.match_type,
            match_value=body.match_value,
            classification_type=body.classification_type,
            notes=body.notes,
            is_active=body.is_active,
        )
    except Exception as exc:
        if "concurrency" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Record was modified by another user. Please refresh and try again.",
            )
        raise

    if not override:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Override not found: {public_id}",
        )
    return override.to_dict()


@router.delete("/classification-overrides/{public_id}")
def delete_override(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """Delete a classification override."""
    svc = ClassificationOverrideService()
    deleted = svc.delete(public_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Override not found: {public_id}",
        )
    return {"deleted": True}
