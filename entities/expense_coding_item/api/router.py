# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query, status

# Local Imports
from entities.expense_coding_item.api.schemas import FlagExpenseCodingItemRequest
from entities.expense_coding_item.business.service import ExpenseCodingItemService
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from shared.api.responses import item_response, list_response, raise_database_error, raise_not_found
from shared.authz import current_user_id
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "expense-coding"])


@router.get("/expense-coding/queue")
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


@router.get("/expense-coding/metrics")
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
    return item_response(metrics)


@router.post("/expense-coding/{public_id}/claim")
def claim_expense_coding_item_router(
    public_id: str,
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    user_id = current_user_id.get()
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authenticated user required.")

    service = ExpenseCodingItemService()
    existing = service.read_by_public_id(public_id)
    if existing is None:
        raise_not_found("Expense coding item")

    try:
        claimed = service.claim(public_id=public_id, user_id=user_id)
    except Exception as error:
        logger.exception("Failed to claim expense coding item %s.", public_id)
        raise_database_error(error)

    if claimed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Expense coding item is currently claimed by another user.",
        )

    return item_response(claimed.to_dict())


@router.post("/expense-coding/{public_id}/release")
def release_expense_coding_item_router(
    public_id: str,
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    user_id = current_user_id.get()
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authenticated user required.")

    service = ExpenseCodingItemService()
    existing = service.read_by_public_id(public_id)
    if existing is None:
        raise_not_found("Expense coding item")

    try:
        released = service.release(public_id=public_id, user_id=user_id)
    except Exception as error:
        logger.exception("Failed to release expense coding item %s.", public_id)
        raise_database_error(error)

    if released is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Expense coding item is not claimed by the current user.",
        )

    return item_response(released.to_dict())


@router.post("/expense-coding/{public_id}/flag")
def flag_expense_coding_item_router(
    public_id: str,
    body: FlagExpenseCodingItemRequest,
    _: dict = Depends(require_module_api(Modules.EXPENSES, "can_update")),
):
    user_id = current_user_id.get()
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authenticated user required.")

    try:
        flagged = ExpenseCodingItemService().record_flag(
            public_id=public_id,
            reason=body.reason,
            modified_by_user_id=user_id,
        )
    except Exception as error:
        logger.exception("Failed to flag expense coding item %s.", public_id)
        raise_database_error(error)

    if flagged is None:
        raise_not_found("Expense coding item")

    return item_response(flagged.to_dict())
