# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query, status

# Local Imports
from entities.bill_credit.api.schemas import BillCreditCreate, BillCreditUpdate
from shared.api.money import to_decimal_or_none
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit.business.complete_service import BillCreditCompleteService
from shared.lifecycle.resolver import attach_lifecycle
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from entities.review.business.completion import gate_completion
from entities.review.business.model import ParentType
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "bill_credit"])


@router.post("/create/bill-credit")
def create_bill_credit_router(body: BillCreditCreate, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_create"))):
    """
    Create a new bill credit.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "credit_date": body.credit_date,
            "credit_number": body.credit_number,
            "total_amount": to_decimal_or_none(body.total_amount),
            "memo": body.memo,
            # U-458: always True. Design §4.2 — create-as-completed is system_authz
            # only; the QBO pull connectors and CLI sync reach it through the SERVICE
            # layer, which keeps its parameter. An external caller can no longer mint
            # an already-completed document that skipped completion entirely.
            "is_draft": True,
        },
        workflow_type="bill_credit_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create bill credit")
    
    return item_response(result.get("data"))


def _current_review_for_bill_credit(bill_credit_id):
    """Latest Review row for one BillCredit, or None when it has none.

    RAISES on a DB failure, deliberately — mirroring Bill's helper and the
    Codex finding behind it. Swallowing it is indistinguishable from "never
    submitted", so a credit sitting in a review queue would render as `draft` /
    `review_status: null` during a blip.
    """
    if not bill_credit_id:
        return None
    from entities.review.persistence.repo import ReviewRepository
    return ReviewRepository().read_current_by_bill_credit_id(bill_credit_id)


def _bill_credit_dict_with_lifecycle(bill_credit, *, review=None) -> dict:
    """Serialize a BillCredit and stamp the derived `status` + `review_status*`.

    U-457. DERIVED per request from IsDraft x the latest Review — BillCredit has
    no Status column (LS-03b, not built), which is why this phase adds no
    `?status=` filter: post-filtering a paginated page would make `count` lie.
    Inherits U-455's frozen `ReviewKind` from day one.
    """
    return attach_lifecycle(
        bill_credit.to_dict(),
        is_draft=bill_credit.is_draft,
        review=review,
        stored_status=getattr(bill_credit, "status", None),
    )


@router.get("/get/bill-credits")
def get_bill_credits_router(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    is_draft: Optional[bool] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS)),
):
    """
    Read bill credits with pagination + filters.

    Mirrors `GET /get/bills` so agent tooling can search consistently
    across transactional entities. Service layer (`read_paginated` +
    `count`) was already in place; this route just wires the filters
    through. Backwards-compatible — bare GET still works (defaults
    page=1, page_size=50, no filters).
    """
    service = BillCreditService()
    bill_credits = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        vendor_id=vendor_id,
        is_draft=is_draft,
    )
    total = service.count(
        search_term=search,
        vendor_id=vendor_id,
        is_draft=is_draft,
    )
    # ONE lookup for the whole page (U-457) — resolving per row is the N+1
    # Bill's slice avoided and pinned.
    from entities.review.persistence.repo import ReviewRepository
    bc_ids = [bc.id for bc in bill_credits if bc.id is not None]
    review_map = ReviewRepository().read_current_by_bill_credit_ids(bc_ids) if bc_ids else {}
    return {
        "data": [
            _bill_credit_dict_with_lifecycle(bc, review=review_map.get(bc.id))
            for bc in bill_credits
        ],
        "count": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/get/bill-credit/by-credit-number-and-vendor")
def get_bill_credit_by_credit_number_and_vendor_router(credit_number: str, vendor_public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read a bill credit by credit number and vendor public ID.
    """
    bill_credit = BillCreditService().read_by_credit_number_and_vendor_public_id(credit_number=credit_number, vendor_public_id=vendor_public_id)
    if bill_credit:
        return item_response(
            _bill_credit_dict_with_lifecycle(
                bill_credit, review=_current_review_for_bill_credit(bill_credit.id)
            )
        )
    return None


@router.get("/get/bill-credit/{public_id}")
def get_bill_credit_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read a bill credit by public ID.
    """
    bill_credit = BillCreditService().read_by_public_id(public_id=public_id)
    if not bill_credit:
        raise_not_found("Bill credit")
    return item_response(
        _bill_credit_dict_with_lifecycle(
            bill_credit, review=_current_review_for_bill_credit(bill_credit.id)
        )
    )


@router.put("/update/bill-credit/{public_id}")
def update_bill_credit_by_public_id_router(public_id: str, body: BillCreditUpdate, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_update"))):
    """
    Update a bill credit by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "vendor_public_id": body.vendor_public_id,
            "credit_date": body.credit_date,
            "credit_number": body.credit_number,
            "total_amount": to_decimal_or_none(body.total_amount),
            "memo": body.memo,
            # U-458: the UPDATE path no longer carries is_draft. Passing None
            # makes the sproc's `CASE WHEN @IsDraft IS NULL` preserve the stored
            # value. Completing is `POST /complete/*` ONLY — which is where the
            # lifecycle gate lives. Letting `can_update` flip this field was a
            # gate bypass (Codex P0): it marked the document completed without
            # `can_complete` and without any review check. Bill has been immune
            # since U-446 made its IsDraft a computed column; this is the same
            # property enforced at the edge for the three that still write it.
            "is_draft": None,
        },
        workflow_type="bill_credit_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update bill credit")
    
    return item_response(result.get("data"))


@router.delete("/delete/bill-credit/{public_id}")
def delete_bill_credit_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_delete"))):
    """
    Delete a bill credit by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="bill_credit_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete bill credit")
    
    return item_response(result.get("data"))


@router.post("/complete/bill-credit/{public_id}")
def complete_bill_credit_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_complete"))):
    """
    Complete a bill credit: finalize and upload attachments to module folders.
    """
    bill_credit = BillCreditService().read_by_public_id(public_id=public_id)
    if not bill_credit:
        raise_not_found("Bill credit")
    if not getattr(bill_credit, "is_draft", True):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bill credit is already completed")

    # U-458 completion gate — see the note on the Bill route. Ships `off`.
    gate_completion(
        parent_type=ParentType.BILL_CREDIT,
        parent_public_id=public_id,
        module_name=Modules.BILL_CREDITS,
        current_user=current_user,
        resolve_review=lambda: _current_review_for_bill_credit(bill_credit.id),
    )

    from entities.completion_job.business.service import CompletionJobService

    service = BillCreditCompleteService()
    job_service = CompletionJobService()
    job = job_service.enqueue("BillCredit", public_id)
    try:
        result = service.complete_bill_credit(public_id=public_id)
        if job.public_id:
            job_service.mark_success(job.public_id)
    except Exception as e:
        if job.public_id:
            job_service.mark_failure(job.public_id, str(e))
        raise

    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to complete bill credit")
        )

    return result
