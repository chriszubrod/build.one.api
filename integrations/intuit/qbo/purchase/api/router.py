# Python Standard Library Imports
import logging
# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.purchase.api.schemas import QboPurchaseSync
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import PurchaseExpenseRepository
from integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo import PurchaseLineExpenseLineItemRepository
from entities.expense.business.service import ExpenseService
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.auth.business.service import get_current_user_api as get_current_qbo_purchase_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-purchase"])
service = QboPurchaseService()


@router.post("/sync/qbo-purchases")
def sync_qbo_purchases_router(body: QboPurchaseSync, current_user: dict = Depends(get_current_qbo_purchase_api)):
    """
    Sync Purchases from QBO.
    """
    purchases = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        start_date=body.start_date,
        end_date=body.end_date,
        sync_to_modules=body.sync_to_modules
    )
    return [purchase.to_dict() for purchase in purchases]


@router.get("/get/qbo-purchases/realm/{realm_id}")
def get_qbo_purchases_by_realm_id_router(realm_id: str, current_user: dict = Depends(get_current_qbo_purchase_api)):
    """
    Read all QBO purchases by realm ID.
    """
    purchases = service.read_by_realm_id(realm_id=realm_id)
    return [purchase.to_dict() for purchase in purchases]


@router.get("/get/qbo-purchase/qbo-id/{qbo_id}")
def get_qbo_purchase_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(get_current_qbo_purchase_api)):
    """
    Read a QBO purchase by QBO ID.
    """
    purchase = service.read_by_qbo_id(qbo_id=qbo_id)
    return purchase.to_dict() if purchase else None


@router.get("/get/qbo-purchases")
def get_qbo_purchases_router(current_user: dict = Depends(get_current_qbo_purchase_api)):
    """
    Read all QBO purchases.
    """
    purchases = service.read_all()
    return [purchase.to_dict() for purchase in purchases]


@router.get("/get/qbo-purchase/{id}")
def get_qbo_purchase_by_id_router(id: int, current_user: dict = Depends(get_current_qbo_purchase_api)):
    """
    Read a QBO purchase by ID.
    """
    purchase = service.read_by_id(id=id)
    return purchase.to_dict() if purchase else None


@router.get("/get/qbo-purchase/{id}/lines")
def get_qbo_purchase_lines_router(id: int, current_user: dict = Depends(get_current_qbo_purchase_api)):
    """
    Read all QBO purchase lines for a purchase.
    """
    lines = service.read_lines_by_qbo_purchase_id(qbo_purchase_id=id)
    return [line.to_dict() for line in lines]


@router.post("/cancel-expense-from-qbo-purchase/{expense_public_id}")
def cancel_expense_from_qbo_purchase_router(
    expense_public_id: str,
    current_user: dict = Depends(get_current_qbo_purchase_api),
):
    """
    Unlink and delete an expense created from QBO purchase, so the purchase shows again in needing update list.
    """
    expense = ExpenseService().read_by_public_id(public_id=expense_public_id)
    if not expense:
        raise HTTPException(status_code=404, detail=f"Expense not found")
    expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id

    pe_repo = PurchaseExpenseRepository()
    pleli_repo = PurchaseLineExpenseLineItemRepository()
    mapping = pe_repo.read_by_expense_id(expense_id=expense_id)
    if not mapping:
        raise HTTPException(status_code=400, detail="Expense is not linked to a QBO purchase")

    # Delete PurchaseLineExpenseLineItem mappings first
    line_items = ExpenseLineItemService().read_by_expense_id(expense_id=expense_id)
    for li in line_items:
        pleli = pleli_repo.read_by_expense_line_item_id(expense_line_item_id=li.id)
        if pleli:
            pleli_repo.delete_by_id(pleli.id)

    # Delete PurchaseExpense mapping
    pe_repo.delete_by_id(mapping.id)

    # Delete the expense (cascades to line items, attachments, etc.)
    ExpenseService().delete_by_public_id(public_id=expense_public_id)

    return {"status": "cancelled", "redirect": "/expense/list"}


@router.post("/ensure-expense-from-qbo-purchase/{qbo_purchase_id}")
def ensure_expense_from_qbo_purchase_router(
    qbo_purchase_id: int,
    current_user: dict = Depends(get_current_qbo_purchase_api),
):
    """
    Ensure Expense and ExpenseLineItems exist for this QBO purchase, then return expense_public_id for redirect.
    """
    purchase = service.read_by_id(id=qbo_purchase_id)
    if not purchase:
        raise HTTPException(status_code=404, detail=f"QBO purchase with id {qbo_purchase_id} not found")
    lines = service.read_lines_by_qbo_purchase_id(qbo_purchase_id=qbo_purchase_id)
    try:
        connector = PurchaseExpenseConnector()
        expense = connector.sync_from_qbo_purchase(purchase, lines)
        return {"expense_public_id": expense.public_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error ensuring expense from QBO purchase {qbo_purchase_id}")
        raise HTTPException(status_code=500, detail=str(e))


