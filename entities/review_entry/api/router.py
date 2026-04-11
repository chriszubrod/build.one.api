# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.review_entry.api.schemas import (
    ReviewEntrySubmit,
    ReviewEntryAdvance,
    ReviewEntryDecline,
)
from entities.review_entry.business.service import ReviewEntryService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)
from shared.api.responses import list_response, item_response, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "review"])


# =============================================================================
# Review Workflow Actions
# =============================================================================

@router.post("/review/submit")
def submit_for_review_router(
    body: ReviewEntrySubmit,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_create")),
):
    """
    Submit a bill for review. Creates the first review entry with the initial status.
    """
    try:
        service = ReviewEntryService()
        entry = service.submit_for_review(
            bill_public_id=body.bill_public_id,
            user_id=current_user.get("id"),
            comments=body.comments,
        )
        return item_response(entry.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/review/advance")
def advance_review_router(
    body: ReviewEntryAdvance,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_complete")),
):
    """
    Advance a bill's review to the next status in the workflow.
    """
    try:
        service = ReviewEntryService()
        entry = service.advance_status(
            bill_public_id=body.bill_public_id,
            user_id=current_user.get("id"),
            comments=body.comments,
        )
        return item_response(entry.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/review/decline")
def decline_review_router(
    body: ReviewEntryDecline,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_complete")),
):
    """
    Decline a bill's review.
    """
    try:
        service = ReviewEntryService()
        entry = service.decline(
            bill_public_id=body.bill_public_id,
            review_status_public_id=body.review_status_public_id,
            user_id=current_user.get("id"),
            comments=body.comments,
        )
        return item_response(entry.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =============================================================================
# Status Query Endpoints
# =============================================================================

@router.get("/review/bill/{bill_public_id}/status")
def get_bill_review_status_router(
    bill_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """
    Get the current review status for a bill.
    """
    service = ReviewEntryService()
    entry = service.get_current_status(bill_public_id=bill_public_id)
    if not entry:
        return {"status": None, "message": "No review entries found for this bill."}
    return item_response(entry.to_dict())


@router.get("/review/bill/{bill_public_id}/timeline")
def get_bill_review_timeline_router(
    bill_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """
    Get the full review timeline for a bill.
    """
    service = ReviewEntryService()
    entries = service.get_timeline(bill_public_id=bill_public_id)
    return list_response([entry.to_dict() for entry in entries])


# =============================================================================
# Standard CRUD Endpoints
# =============================================================================

@router.get("/get/review-entries")
def get_review_entries_router(
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """
    Read all review entries.
    """
    entries = ReviewEntryService().read_all()
    return list_response([entry.to_dict() for entry in entries])


@router.get("/get/review-entry/{public_id}")
def get_review_entry_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    """
    Read a review entry by public ID.
    """
    entry = ReviewEntryService().read_by_public_id(public_id=public_id)
    if not entry:
        raise_not_found("Review entry")
    return item_response(entry.to_dict())
