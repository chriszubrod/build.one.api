# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query, status

# Local Imports
from entities.coding_suggestion.api.schemas import (
    ConfirmCodingSuggestionRequest,
    FlagCodingSuggestionRequest,
)
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.coding_suggestion.business.suggestion_service import CodingSuggestionEngine
from integrations.intuit.qbo.base.client import recode_write_gate_reason
from integrations.intuit.qbo.outbox.business.service import QboOutboxDeadLetterExistsError
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from shared.access import (
    EntityNotAccessibleError,
    assert_can_access_project,
)
from shared.api.errors import ApiError, ErrorCode
from shared.api.responses import item_response, list_response, raise_database_error, raise_not_found
from shared.lifecycle.completion_gate import HoldingCodeRefused
from shared.authz import current_user_id
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "expense-coding"])


def _assert_can_access_coding_suggestion(service: CodingSuggestionService, suggestion) -> None:
    """U-652d4: the row-level check lives in CodingSuggestionService.assert_accessible (the service is the
    gate for every caller). The router only maps a refusal to 404 so the URL does not confirm existence."""
    try:
        service.assert_accessible(suggestion)
    except EntityNotAccessibleError:
        raise_not_found("Expense coding item")


@router.get(
    "/expense-coding/queue",
    deprecated=True,
    description="Deprecated. Use GET /api/v1/get/expense/coding/queue instead.",
)
def get_expense_coding_queue_router(
    realm_id: Optional[str] = Query(default=None),
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_read")),
):
    try:
        rows = QboPurchaseService().get_expense_coding_queue(realm_id=realm_id)
    except Exception as error:
        logger.exception("Failed to read expense coding queue.")
        raise HTTPException(status_code=500, detail="Failed to read expense coding queue.") from error
    return list_response(rows)


@router.post("/expense/coding/suggest")
@router.post(
    "/expense-coding/suggest",
    deprecated=True,
    description="Deprecated. Use POST /api/v1/expense/coding/suggest instead.",
)
def suggest_expense_coding_items_router(
    realm_id: Optional[str] = Query(default=None),
    max_items: int = Query(default=200, ge=1, le=1000),
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    try:
        counts = CodingSuggestionEngine().suggest_pending(
            realm_id=realm_id,
            max_items=max_items,
        )
    except Exception as error:
        logger.exception("Failed to run expense coding suggestions.")
        raise HTTPException(
            status_code=500,
            detail="Failed to run expense coding suggestions.",
        ) from error
    return item_response(counts)


@router.get(
    "/expense-coding/metrics",
    deprecated=True,
    description="Deprecated. Use GET /api/v1/get/expense/coding/metrics instead.",
)
def get_expense_coding_metrics_router(
    realm_id: Optional[str] = Query(default=None),
    since_days: Optional[int] = Query(default=None, ge=1),
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_read")),
):
    try:
        metrics = QboPurchaseService().get_expense_coding_metrics(
            realm_id=realm_id,
            since_days=since_days,
        )
    except Exception as error:
        logger.exception("Failed to read expense coding metrics.")
        raise HTTPException(status_code=500, detail="Failed to read expense coding metrics.") from error
    metrics["recode_writes_enabled"] = recode_write_gate_reason() is None
    return item_response(metrics)


@router.post("/expense/coding/{public_id}/flag")
@router.post(
    "/expense-coding/{public_id}/flag",
    deprecated=True,
    description="Deprecated. Use POST /api/v1/expense/coding/{public_id}/flag instead.",
)
def flag_expense_coding_item_router(
    public_id: str,
    body: FlagCodingSuggestionRequest,
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    user_id = current_user_id.get()
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authenticated user required.")

    service = CodingSuggestionService()

    try:
        flagged = service.record_flag(
            public_id=public_id,
            reason=body.reason,
            modified_by_user_id=user_id,
        )
    except EntityNotAccessibleError:
        raise_not_found("Expense coding item")
    except Exception as error:
        logger.exception("Failed to flag coding suggestion %s.", public_id)
        raise_database_error(error)

    if flagged is None:
        raise_not_found("Expense coding item")

    payload = service.to_api_dict(flagged)
    return item_response(payload)


@router.post("/expense/coding/{public_id}/confirm")
@router.post(
    "/expense-coding/{public_id}/confirm",
    deprecated=True,
    description="Deprecated. Use POST /api/v1/expense/coding/{public_id}/confirm instead.",
)
def confirm_expense_coding_item_router(
    public_id: str,
    body: ConfirmCodingSuggestionRequest,
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    user_id = current_user_id.get()
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authenticated user required.")

    service = CodingSuggestionService()
    existing = service.read_by_public_id(public_id)
    if existing is None:
        raise_not_found("Expense coding item")

    _assert_can_access_coding_suggestion(service, existing)

    from entities.project.business.service import ProjectService
    from entities.sub_cost_code.business.service import SubCostCodeService

    project = ProjectService().read_by_public_id(body.project_public_id)
    if project is None:
        raise_not_found("Project")

    # Defense in depth: the project read above is already access-filtered, so an inaccessible
    # project normally arrives as "not found"; keep the same body if the two filters ever diverge.
    try:
        assert_can_access_project(project.id)
    except EntityNotAccessibleError:
        raise_not_found("Project")

    sub_cost_code = SubCostCodeService().read_by_public_id(body.sub_cost_code_public_id)
    if sub_cost_code is None:
        raise_not_found("Sub cost code")

    # U-652: refuse a multi-line purchase before ANY write (the claim is a write).
    try:
        multi_line = service.is_multi_line_purchase(existing)
    except Exception as error:
        logger.exception("Failed to count purchase lines for coding suggestion %s.", public_id)
        raise_database_error(error)
    if multi_line:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="multi_line_purchase_confirm_unsupported",
        )

    # U-652d4 item 10: the write gate is checked BEFORE the claim (a claim is a durable write). A closed
    # gate is the same 422 confirm() returns, with zero claim/confirm calls. The in-service check stays
    # as the backstop.
    gate_reason = recode_write_gate_reason()
    if gate_reason is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=gate_reason)

    claimed_here = False
    if existing.claimed_by_user_id != user_id:
        try:
            claimed = service.claim(public_id=public_id, user_id=user_id)
        except Exception as error:
            logger.exception("Failed to claim coding suggestion %s before confirm.", public_id)
            raise_database_error(error)

        if claimed is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Expense coding item is currently claimed by another user.",
            )
        claimed_here = True

    def _release_if_claimed(message: str = "Failed to release claim on coding suggestion %s after refusal.") -> None:
        """Give back a claim taken by THIS request (a refusal must not lock the user out)."""
        if not claimed_here:
            return
        try:
            service.release(public_id=public_id, user_id=user_id)
        except Exception:
            logger.exception(message, public_id)

    try:
        result = service.confirm(
            public_id=public_id,
            project_id=project.id,
            sub_cost_code_id=sub_cost_code.id,
            description=body.description,
            was_overridden=body.was_overridden,
            user_id=user_id,
        )
    except QboOutboxDeadLetterExistsError as error:
        _release_if_claimed()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except EntityNotAccessibleError:
        # U-652d4 item 14: the service refused a row the actor cannot see (a race past the router check).
        _release_if_claimed()
        raise_not_found("Expense coding item")
    except HoldingCodeRefused:
        _release_if_claimed()
        raise
    except Exception as error:
        logger.exception("Failed to confirm coding suggestion %s.", public_id)
        # A DB error must not strand the claim taken here for the 900 s reclaim window.
        _release_if_claimed("Failed to release claim on coding suggestion %s after confirm error.")
        raise_database_error(error)

    result_status = result.get("status")
    if result_status == "not_found":
        _release_if_claimed()
        raise_not_found("Expense coding item")
    if result_status == "multi_line_unsupported":
        # A second uncategorized line appeared after the pre-check (concurrent QBO pull).
        # Give back a claim taken by THIS request so the refusal locks nobody out.
        _release_if_claimed()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.get("reason", "multi_line_purchase_confirm_unsupported"),
        )
    if result_status == "busy":
        # U-551b-f1: a drain holds the suggestion's lock (a QBO write is in flight). Nothing was
        # written; give back a claim taken by THIS request so the retry is not locked out.
        _release_if_claimed()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="coding_suggestion_busy")
    if result_status in ("invalid", "mapping_missing", "writes_disabled"):
        _release_if_claimed()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.get("reason", "Invalid confirmation request."),
        )

    if result_status != "enqueued":
        # U-551b-f2: the 200 path is ONLY an enqueued confirm (incl. a ``recovered`` one). Every other
        # status is a refusal, never a silent 200: the status guard's already_written / resolved_externally /
        # repoint_pending / already_enqueued / already_terminal (zero writes), not_recorded, and any
        # unmapped future status. Give back a claim taken by THIS request.
        _release_if_claimed()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result_status or "confirm_refused",
        )

    item = service.read_by_public_id(public_id)
    if item is None:
        raise_not_found("Expense coding item")

    payload = service.to_api_dict(item)
    payload["enqueued"] = result.get("enqueued", False)
    payload["recovered"] = bool(result.get("recovered", False))

    return item_response(payload)
