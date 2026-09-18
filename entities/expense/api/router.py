# Python Standard Library Imports
import logging
import time
from datetime import date
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from decimal import Decimal

# Local Imports
from entities.expense.api.schemas import ExpenseCreate, ExpenseUpdate
from entities.expense.business.service import ExpenseService
from shared.api.errors import ApiError, ErrorCode
from shared.api.responses import list_response, item_response, accepted_response, raise_workflow_error, raise_not_found
from shared.lifecycle.resolver import LIFECYCLE_STATUSES, attach_lifecycle
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

from entities.review.business.completion import gate_completion
from entities.review.business.model import ParentType

logger = logging.getLogger(__name__)

# Terminal coding statuses — everything else counts as OPEN for `needs_coding`.
# `written` is the sole terminal state; do not derive openness from SubCostCodeId.
EXPENSE_CODING_TERMINAL_STATUSES = frozenset({"written"})

router = APIRouter(prefix="/api/v1", tags=["api", "expense"])

# Cache last completion result per expense (TTL 1 hour)
_EXPENSE_COMPLETION_RESULT_CACHE: dict[str, dict] = {}
_EXPENSE_COMPLETION_CACHE_TTL_SEC = 3600


def _clean_expense_completion_cache():
    now = time.time()
    expired = [k for k, v in _EXPENSE_COMPLETION_RESULT_CACHE.items() if v.get("expires_at", 0) < now]
    for k in expired:
        del _EXPENSE_COMPLETION_RESULT_CACHE[k]


@router.post("/create/expense")
def create_expense_router(body: ExpenseCreate, current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_create"))):
    """
    Create a new expense.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "expense_date": body.expense_date,
            "reference_number": body.reference_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            "memo": body.memo,
            # U-458: always True. Design §4.2 — create-as-completed is system_authz
            # only; the QBO pull connectors and CLI sync reach it through the SERVICE
            # layer, which keeps its parameter. An external caller can no longer mint
            # an already-completed document that skipped completion entirely.
            "is_draft": True,
            # Was previously dropped here — is_credit silently defaulted to False
            # so refunds created via the API/agent never stuck. Now threaded.
            "is_credit": body.is_credit if body.is_credit is not None else False,
            "source_email_message_public_id": body.source_email_message_public_id,
            "attachment_public_id": body.attachment_public_id,
            # Inline summary-line fields (all optional). When provided, the
            # service populates the auto-created placeholder ExpenseLineItem so
            # agent / folder flows don't need a follow-up update.
            "line_description": body.line_description,
            "line_quantity": body.line_quantity,
            "line_rate": Decimal(str(body.line_rate)) if body.line_rate is not None else None,
            "line_amount": Decimal(str(body.line_amount)) if body.line_amount is not None else None,
            "line_markup": Decimal(str(body.line_markup)) if body.line_markup is not None else None,
            "line_price": Decimal(str(body.line_price)) if body.line_price is not None else None,
            "line_is_billable": body.line_is_billable,
            "line_sub_cost_code_id": body.line_sub_cost_code_id,
            "line_project_public_id": body.line_project_public_id,
        },
        workflow_type="expense_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create expense")

    return item_response(result.get("data"))


def _current_review_for_expense(expense_id):
    """Latest Review row for one Expense, or None when it has none.

    RAISES on a DB failure, deliberately — mirroring Bill's helper and the
    Codex finding behind it (2026-09-11). Swallowing it and returning None is
    indistinguishable from "never submitted", so an expense sitting in
    someone's review queue would render as `draft` / `review_status: null`
    during a blip: a wrong answer about a money document dressed up as a right
    one. The expense row itself came from this same database microseconds
    earlier, so the availability a swallow buys is ~zero.
    """
    if not expense_id:
        return None
    from entities.review.persistence.repo import ReviewRepository
    return ReviewRepository().read_current_by_expense_id(expense_id)


def build_expense_coding_block(items: list[dict] | None) -> dict:
    """Compose the `coding` block for an expense read payload (U-480).

    `needs_coding` derives from ExpenseCodingItem.Status only — never from
    ExpenseLineItem.SubCostCodeId (the post-recode / pre-pull window can leave
    the line coded locally while the coding item is still open).
    """
    rows = items or []
    open_items = [
        item for item in rows
        if item.get("status") not in EXPENSE_CODING_TERMINAL_STATUSES
    ]
    return {
        "needs_coding": len(open_items) > 0,
        "open_items": len(open_items),
        "items": [
            {
                "public_id": item["public_id"],
                "status": item["status"],
                "confidence": item.get("confidence"),
                "suggested_project_id": item.get("suggested_project_id"),
                "suggested_sub_cost_code_id": item.get("suggested_sub_cost_code_id"),
                "flag_reason": item.get("flag_reason"),
            }
            for item in rows
        ],
    }


def _expense_dict_with_lifecycle(expense, *, review=None, coding_items=None) -> dict:
    """Serialize an Expense and stamp `status` + `review_status*`.

    U-467: `status` is the STORED `Expense.Status` column where one exists
    (it WINS over the derived value, because it is what the `?status=` filter
    selected on). The derivation remains as the fallback for a row predating
    the backfill and as the value the parity check compares against.
    """
    return attach_lifecycle(
        expense.to_dict(),
        is_draft=expense.is_draft,
        review=review,
        stored_status=getattr(expense, "status", None),
    ) | {"coding": build_expense_coding_block(coding_items)}


@router.get("/get/expenses")
def get_expenses_router(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    is_draft: Optional[bool] = Query(default=None),
    start_date: Optional[date] = Query(default=None, description="Inclusive lower bound on expense_date (YYYY-MM-DD)."),
    end_date: Optional[date] = Query(default=None, description="Inclusive upper bound on expense_date (YYYY-MM-DD)."),
    status: Optional[str] = Query(
        default=None,
        description=(
            "Filter by lifecycle status: draft, submitted, in_review, approved, "
            "declined or completed. Filters in SQL, so `count` stays truthful."
        ),
    ),
    current_user: dict = Depends(require_module_api(Modules.EXPENSES)),
):
    """
    Read expenses with pagination + filters.

    `status` (U-467) filters on the stored `Expense.Status` column inside the
    paginated sproc. That is the whole reason the column exists: U-457 derived
    the same value per request, but post-filtering an already-paginated page
    would have made `count` describe a different set than `data`.
    """
    if isinstance(status, str) and status not in LIFECYCLE_STATUSES:
        raise ApiError(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown status {status!r}. Expected one of: {', '.join(LIFECYCLE_STATUSES)}.",
            error_code=ErrorCode.VALIDATION_ERROR,
        )
    service = ExpenseService()
    expenses, total = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        vendor_id=vendor_id,
        is_draft=is_draft,
        status=status if isinstance(status, str) else None,
        start_date=start_date.isoformat() if isinstance(start_date, date) else None,
        end_date=end_date.isoformat() if isinstance(end_date, date) else None,
    )
    # ONE lookup for the whole page (U-457). Resolving per row is the N+1 that
    # Bill's slice avoided and pinned; the batch sproc exists for this.
    from entities.review.persistence.repo import ReviewRepository
    from entities.expense_coding_item.persistence.repo import ExpenseCodingItemRepository
    expense_ids = [e.id for e in expenses if e.id is not None]
    review_map = ReviewRepository().read_current_by_expense_ids(expense_ids) if expense_ids else {}
    coding_map = (
        ExpenseCodingItemRepository().read_state_by_expense_ids(expense_ids)
        if expense_ids
        else {}
    )
    return {
        "data": [
            _expense_dict_with_lifecycle(
                e,
                review=review_map.get(e.id),
                coding_items=coding_map.get(e.id),
            )
            for e in expenses
        ],
        "count": total,
        "page": page,
        "page_size": page_size,
    }


from entities.expense_coding_item.api.router import (
    get_expense_coding_metrics_router,
    get_expense_coding_queue_router,
)

# Literal `/get/expense/coding/*` segments MUST register before `/get/expense/{public_id}`
# (app mounts this router before expense_coding_item — see U-481 / Bill parity).
router.add_api_route(
    "/get/expense/coding/queue",
    get_expense_coding_queue_router,
    methods=["GET"],
    tags=router.tags,
)
router.add_api_route(
    "/get/expense/coding/metrics",
    get_expense_coding_metrics_router,
    methods=["GET"],
    tags=router.tags,
)


@router.get("/get/expense/{public_id}/coding")
def get_expense_public_id_coding_router(
    public_id: str,
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_read")),
):
    """Coding state for one expense (may include several coded lines)."""
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    if not expense:
        raise_not_found("Expense")
    from entities.expense_coding_item.persistence.repo import ExpenseCodingItemRepository

    coding_map = (
        ExpenseCodingItemRepository().read_state_by_expense_ids([expense.id])
        if expense.id is not None
        else {}
    )
    return item_response(build_expense_coding_block(coding_map.get(expense.id)))


@router.get("/get/expense/by-reference-number-and-vendor")
def get_expense_by_reference_number_and_vendor_router(reference_number: str, vendor_public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES))):
    """
    Read an expense by reference number and vendor public ID.
    """
    expense = ExpenseService().read_by_reference_number_and_vendor_public_id(reference_number=reference_number, vendor_public_id=vendor_public_id)
    if not expense:
        raise_not_found("Expense")
    return item_response(
        _expense_dict_with_lifecycle(expense, review=_current_review_for_expense(expense.id))
    )


@router.get("/get/expense/{public_id}/completion-result")
def get_expense_completion_result_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES))):
    """
    Return the last completion result for an expense (Build One, SharePoint).
    Used by the list page to show step status after an expense completes in the background.
    In-memory cache only (1 hour TTL). Returns 404 if no result or expired.
    """
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    if not expense:
        raise_not_found("Expense")
    _clean_expense_completion_cache()
    entry = _EXPENSE_COMPLETION_RESULT_CACHE.get(public_id)
    if not entry or entry.get("expires_at", 0) < time.time():
        raise_not_found("Completion result")
    return item_response(entry["result"])


@router.get("/get/expense/{public_id}")
def get_expense_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES))):
    """
    Read an expense by public ID.
    """
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    if not expense:
        raise_not_found("Expense")
    from entities.expense_coding_item.persistence.repo import ExpenseCodingItemRepository

    coding_map = (
        ExpenseCodingItemRepository().read_state_by_expense_ids([expense.id])
        if expense.id is not None
        else {}
    )
    return item_response(
        _expense_dict_with_lifecycle(
            expense,
            review=_current_review_for_expense(expense.id),
            coding_items=coding_map.get(expense.id),
        )
    )


@router.put("/update/expense/{public_id}")
def update_expense_by_public_id_router(public_id: str, body: ExpenseUpdate, current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_update"))):
    """
    Update an expense by public ID.
    
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
            "expense_date": body.expense_date,
            "reference_number": body.reference_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount is not None else None,
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
            # Was dropped here too (same bug-class as the create path). The
            # service applies is_credit only when not None, so an unchanged
            # PUT (is_credit=None) is a no-op — but a toggle now sticks.
            "is_credit": body.is_credit,
        },
        workflow_type="expense_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update expense")

    return item_response(result.get("data"))


@router.delete("/delete/expense/{public_id}")
def delete_expense_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_delete"))):
    """
    Delete an expense by public ID.

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
        workflow_type="expense_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete expense")

    return item_response(result.get("data"))


def _run_complete_expense(public_id: str, job_public_id: str | None = None, force: bool = False) -> None:
    """Background task: run expense completion (finalize, SharePoint).

    `force` is reclaim-only: the watchdog re-drives an already-finalized (non-draft)
    expense, so it must bypass the "already completed" skip. The happy path never sets it.
    """
    from entities.completion_job.business.service import CompletionJobService

    job_service = CompletionJobService()
    try:
        expense = ExpenseService().read_by_public_id(public_id=public_id)
        if not expense:
            # U-435: a job pointing at an Expense that no longer exists is an
            # anomaly, not a success. mark_failure lets the watchdog re-check and
            # then dead-letter it at max_attempts (5) so it surfaces.
            logger.warning("Complete expense: no such expense, marking job failed: public_id=%s", public_id)
            if job_public_id:
                job_service.mark_failure(job_public_id, "Expense not found")
            return
        if not force and not getattr(expense, "is_draft", True):
            # Genuinely already done — an idempotent no-op, so success is correct.
            logger.info("Complete expense skipped (already completed): public_id=%s", public_id)
            if job_public_id:
                job_service.mark_success(job_public_id)
            return
        result = ExpenseService().complete_expense(public_id=public_id)
        logger.info(
            "Complete expense background result: public_id=%s, status_code=%s, expense_finalized=%s",
            public_id, result.get("status_code"), result.get("expense_finalized"),
        )
        expires_at = time.time() + _EXPENSE_COMPLETION_CACHE_TTL_SEC
        _EXPENSE_COMPLETION_RESULT_CACHE[public_id] = {
            "result": result,
            "expires_at": expires_at,
        }
        if result.get("status_code") >= 400:
            logger.warning("Complete expense failed in background: %s", result.get("message"))
        if job_public_id:
            # U-435 — the marking contract, corrected (U-434's fix, ported).
            #
            # This called mark_success() for ANY returned dict, on the stated
            # theory that "a returned dict = finalize+enqueue ran". False for
            # complete_expense's early returns (404 missing, 400, 500 finalize
            # error): they return BEFORE the finalize and before the outbox
            # enqueue, so nothing was queued and nothing retries. Marking them
            # successful retired the job, and claim_next_stuck keys on job status
            # — so the reclaim watchdog skipped them forever. The expense stayed
            # IsDraft=1 and its receipt never reached SharePoint/Box/QBO, while
            # the client had already been handed a 202.
            #
            # `expense_finalized` is the correct discriminator: True on every path
            # past the finalize (including 207 partial-success, where the outbox
            # legitimately owns the retries) and False on exactly the early returns.
            if result.get("expense_finalized"):
                job_service.mark_success(job_public_id)
            else:
                job_service.mark_failure(
                    job_public_id,
                    f"completion returned {result.get('status_code')} before enqueue: "
                    f"{result.get('message')}",
                )
    except Exception as e:
        logger.exception("Complete expense background task failed: public_id=%s", public_id)
        failure_result = {
            "status_code": 500,
            "message": str(e),
            "expense_finalized": False,
            "file_uploads": {},
            "excel_syncs": {},
            "qbo_sync": {},
            "errors": [{"step": "complete_expense", "error": str(e)}],
        }
        expires_at = time.time() + _EXPENSE_COMPLETION_CACHE_TTL_SEC
        _EXPENSE_COMPLETION_RESULT_CACHE[public_id] = {"result": failure_result, "expires_at": expires_at}
        if job_public_id:
            job_service.mark_failure(job_public_id, str(e))


@router.post("/complete/expense/{public_id}")
def complete_expense_router(
    public_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_complete")),
):
    """
    Queue expense completion (finalize, SharePoint). Returns 202 immediately;
    work runs in background. Client can poll GET /api/v1/get/expense/{public_id}/completion-result or use list page banner.
    """
    logger.info("Complete expense API called: public_id=%s (queuing background task)", public_id)
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    if not expense:
        raise_not_found("Expense")
    if not getattr(expense, "is_draft", True):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Expense is already completed")

    # U-458 completion gate — see the note on the Bill route. Ships `off`.
    gate_completion(
        parent_type=ParentType.EXPENSE,
        parent_public_id=public_id,
        module_name=Modules.EXPENSES,
        current_user=current_user,
        resolve_review=lambda: _current_review_for_expense(expense.id),
    )

    from entities.completion_job.business.service import CompletionJobService

    job = CompletionJobService().enqueue("Expense", public_id)
    if job.was_created:
        background_tasks.add_task(_run_complete_expense, public_id, job.public_id)
    # Coalesced job: completion already in flight; crash recovery is via reclaim watchdog.
    return JSONResponse(
        status_code=http_status.HTTP_202_ACCEPTED,
        content=accepted_response(public_id, "expense_public_id"),
    )
