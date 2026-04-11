# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.review_status.api.schemas import ReviewStatusCreate, ReviewStatusUpdate
from entities.review_status.business.service import ReviewStatusService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "review-status"])


@router.post("/create/review-status")
def create_review_status_router(
    body: ReviewStatusCreate,
    current_user: dict = Depends(require_module_api(Modules.REVIEW_STATUSES, "can_create")),
):
    """
    Create a new review status.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "description": body.description,
            "sort_order": body.sort_order,
            "is_final": body.is_final,
            "is_declined": body.is_declined,
            "is_active": body.is_active,
            "color": body.color,
        },
        workflow_type="review_status_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create review status")

    return item_response(result.get("data"))


@router.get("/get/review-statuses")
def get_review_statuses_router(
    current_user: dict = Depends(require_module_api(Modules.REVIEW_STATUSES)),
):
    """
    Read all review statuses.
    """
    review_statuses = ReviewStatusService().read_all()
    return list_response([rs.to_dict() for rs in review_statuses])


@router.get("/get/review-status/{public_id}")
def get_review_status_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.REVIEW_STATUSES)),
):
    """
    Read a review status by public ID.
    """
    review_status = ReviewStatusService().read_by_public_id(public_id=public_id)
    if not review_status:
        raise_not_found("Review status")
    return item_response(review_status.to_dict())


@router.put("/update/review-status/{public_id}")
def update_review_status_by_public_id_router(
    public_id: str,
    body: ReviewStatusUpdate,
    current_user: dict = Depends(require_module_api(Modules.REVIEW_STATUSES, "can_update")),
):
    """
    Update a review status by public ID.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "name": body.name,
            "description": body.description,
            "sort_order": body.sort_order,
            "is_final": body.is_final,
            "is_declined": body.is_declined,
            "is_active": body.is_active,
            "color": body.color,
        },
        workflow_type="review_status_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update review status")

    return item_response(result.get("data"))


@router.delete("/delete/review-status/{public_id}")
def delete_review_status_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.REVIEW_STATUSES, "can_delete")),
):
    """
    Delete a review status by public ID.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="review_status_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete review status")

    return item_response(result.get("data"))
