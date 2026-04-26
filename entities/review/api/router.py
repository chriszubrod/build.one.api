# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.review.api.schemas import (
    ReviewAdvanceRequest,
    ReviewDeclineRequest,
    ReviewSubmitRequest,
)
from entities.review.business.model import ParentType
from entities.review.business.service import (
    ParentNotFoundError,
    ReviewService,
    ReviewTransitionError,
)
from core.workflow.api.process_engine import (
    Channel,
    EventType,
    ProcessEngine,
    TriggerContext,
)
from shared.api.responses import (
    item_response,
    list_response,
    raise_workflow_error,
)
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "review"])


# =============================================================================
# Internal helpers
# =============================================================================

def _execute_create(payload: dict, current_user: dict, default_error: str):
    """
    Fire ProcessEngine with workflow_type='review_create'. The InstantWorkflow
    handler routes the payload to ReviewService.create() and writes both the
    Review row and the Workflow + WorkflowEvent audit rows.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload=payload,
        workflow_type="review_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), default_error)
    return item_response(result.get("data"))


def _do_action(
    *,
    action: str,                    # 'submit' | 'advance' | 'decline'
    parent_type: str,
    parent_public_id: str,
    current_user: dict,
    body,
):
    service = ReviewService()
    try:
        if action == "submit":
            payload = service.build_submit_payload(
                parent_type=parent_type,
                parent_public_id=parent_public_id,
                user_id=current_user["id"],
                comments=body.comments,
            )
        elif action == "advance":
            payload = service.build_advance_payload(
                parent_type=parent_type,
                parent_public_id=parent_public_id,
                user_id=current_user["id"],
                comments=body.comments,
            )
        elif action == "decline":
            payload = service.build_decline_payload(
                parent_type=parent_type,
                parent_public_id=parent_public_id,
                user_id=current_user["id"],
                target_status_public_id=body.target_status_public_id,
                comments=body.comments,
            )
        else:
            raise ValueError(f"Unknown review action: {action}")
    except ParentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ReviewTransitionError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return _execute_create(payload, current_user, f"Failed to {action} review")


def _do_list(parent_type: str, parent_public_id: str):
    try:
        reviews = ReviewService().list_for(parent_type, parent_public_id)
    except ParentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return list_response([r.to_dict() for r in reviews])


# =============================================================================
# Bill
# =============================================================================

@router.post("/submit/review/bill/{public_id}")
def submit_review_bill_router(
    public_id: str,
    body: ReviewSubmitRequest,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_update")),
):
    return _do_action(
        action="submit",
        parent_type=ParentType.BILL,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/advance/review/bill/{public_id}")
def advance_review_bill_router(
    public_id: str,
    body: ReviewAdvanceRequest,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_update")),
):
    return _do_action(
        action="advance",
        parent_type=ParentType.BILL,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/decline/review/bill/{public_id}")
def decline_review_bill_router(
    public_id: str,
    body: ReviewDeclineRequest,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_update")),
):
    return _do_action(
        action="decline",
        parent_type=ParentType.BILL,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.get("/get/reviews/bill/{public_id}")
def get_reviews_bill_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BILLS)),
):
    return _do_list(ParentType.BILL, public_id)


# =============================================================================
# Expense
# =============================================================================

@router.post("/submit/review/expense/{public_id}")
def submit_review_expense_router(
    public_id: str,
    body: ReviewSubmitRequest,
    current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    return _do_action(
        action="submit",
        parent_type=ParentType.EXPENSE,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/advance/review/expense/{public_id}")
def advance_review_expense_router(
    public_id: str,
    body: ReviewAdvanceRequest,
    current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    return _do_action(
        action="advance",
        parent_type=ParentType.EXPENSE,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/decline/review/expense/{public_id}")
def decline_review_expense_router(
    public_id: str,
    body: ReviewDeclineRequest,
    current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    return _do_action(
        action="decline",
        parent_type=ParentType.EXPENSE,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.get("/get/reviews/expense/{public_id}")
def get_reviews_expense_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EXPENSES)),
):
    return _do_list(ParentType.EXPENSE, public_id)


# =============================================================================
# Bill Credit
# =============================================================================

@router.post("/submit/review/bill-credit/{public_id}")
def submit_review_bill_credit_router(
    public_id: str,
    body: ReviewSubmitRequest,
    current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_update")),
):
    return _do_action(
        action="submit",
        parent_type=ParentType.BILL_CREDIT,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/advance/review/bill-credit/{public_id}")
def advance_review_bill_credit_router(
    public_id: str,
    body: ReviewAdvanceRequest,
    current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_update")),
):
    return _do_action(
        action="advance",
        parent_type=ParentType.BILL_CREDIT,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/decline/review/bill-credit/{public_id}")
def decline_review_bill_credit_router(
    public_id: str,
    body: ReviewDeclineRequest,
    current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_update")),
):
    return _do_action(
        action="decline",
        parent_type=ParentType.BILL_CREDIT,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.get("/get/reviews/bill-credit/{public_id}")
def get_reviews_bill_credit_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS)),
):
    return _do_list(ParentType.BILL_CREDIT, public_id)


# =============================================================================
# Invoice
# =============================================================================

@router.post("/submit/review/invoice/{public_id}")
def submit_review_invoice_router(
    public_id: str,
    body: ReviewSubmitRequest,
    current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_update")),
):
    return _do_action(
        action="submit",
        parent_type=ParentType.INVOICE,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/advance/review/invoice/{public_id}")
def advance_review_invoice_router(
    public_id: str,
    body: ReviewAdvanceRequest,
    current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_update")),
):
    return _do_action(
        action="advance",
        parent_type=ParentType.INVOICE,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.post("/decline/review/invoice/{public_id}")
def decline_review_invoice_router(
    public_id: str,
    body: ReviewDeclineRequest,
    current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_update")),
):
    return _do_action(
        action="decline",
        parent_type=ParentType.INVOICE,
        parent_public_id=public_id,
        current_user=current_user,
        body=body,
    )


@router.get("/get/reviews/invoice/{public_id}")
def get_reviews_invoice_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.INVOICES)),
):
    return _do_list(ParentType.INVOICE, public_id)
