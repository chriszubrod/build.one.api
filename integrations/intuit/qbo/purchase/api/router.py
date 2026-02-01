# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.purchase.api.schemas import QboPurchaseSync, QboPurchasePush
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.attachable.connector.attachment.business.service import AttachableAttachmentConnector
from entities.expense.business.service import ExpenseService
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
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


@router.post("/sync/expense-to-qbo/{expense_public_id}")
def sync_expense_to_qbo_router(
    expense_public_id: str,
    body: QboPurchasePush,
    current_user: dict = Depends(get_current_qbo_purchase_api)
):
    """
    Push a local Expense to QuickBooks Online as a Purchase update.
    
    This endpoint:
    1. Retrieves the local Expense by public_id
    2. Validates the expense is finalized and has a QBO mapping
    3. Updates the Purchase in QBO with ItemBasedExpenseLineDetail lines
    4. Optionally syncs attachments to QBO
    
    Args:
        expense_public_id: Public ID of the local Expense to push
        body: Request body with realm_id and sync options
        
    Returns:
        dict with status, qbo_purchase info, and attachment count
    """
    expense_service = ExpenseService()
    
    # Get the expense
    expense = expense_service.read_by_public_id(public_id=expense_public_id)
    if not expense:
        raise HTTPException(status_code=404, detail=f"Expense with public_id '{expense_public_id}' not found")
    
    # Check if expense is finalized
    if expense.is_draft:
        raise HTTPException(
            status_code=400, 
            detail="Expense must be finalized (is_draft=False) before syncing to QBO"
        )
    
    try:
        # Push to QBO
        connector = PurchaseExpenseConnector()
        qbo_purchase = connector.sync_to_qbo_purchase(expense, body.realm_id)
        
        # Optionally sync attachments
        attachments_synced = 0
        if body.sync_attachments:
            attachments_synced = _sync_expense_attachments(expense, qbo_purchase.qbo_id, body.realm_id)
        
        return {
            "status": "success",
            "expense_id": expense.id,
            "expense_public_id": expense.public_id,
            "qbo_purchase_id": qbo_purchase.qbo_id,
            "qbo_purchase_db_id": qbo_purchase.id,
            "sync_token": qbo_purchase.sync_token,
            "attachments_synced": attachments_synced,
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error pushing Expense {expense_public_id} to QBO")
        raise HTTPException(status_code=500, detail=f"Failed to push expense to QBO: {str(e)}")


def _sync_expense_attachments(expense, qbo_purchase_id: str, realm_id: str) -> int:
    """
    Sync all attachments for an Expense to QBO.
    
    Returns the number of attachments synced.
    """
    expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
    
    expense_line_item_service = ExpenseLineItemService()
    expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
    attachment_service = AttachmentService()
    attachment_connector = AttachableAttachmentConnector()
    
    # Get all line items for this expense
    line_items = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
    if not line_items:
        return 0
    
    attachments_synced = 0
    synced_attachment_ids = set()
    
    for line_item in line_items:
        if not line_item.public_id:
            continue
        
        # Get attachment for this line item
        attachment_link = expense_line_item_attachment_service.read_by_expense_line_item_id(
            expense_line_item_public_id=line_item.public_id
        )
        
        if not attachment_link or not attachment_link.attachment_id:
            continue
        
        # Skip if already synced
        if attachment_link.attachment_id in synced_attachment_ids:
            continue
        
        # Get attachment record
        attachment = attachment_service.read_by_id(id=attachment_link.attachment_id)
        if not attachment or not attachment.blob_url:
            continue
        
        try:
            attachment_connector.sync_attachment_to_qbo(
                attachment=attachment,
                realm_id=realm_id,
                entity_type="Purchase",  # Use Purchase instead of Bill
                entity_id=qbo_purchase_id,
            )
            synced_attachment_ids.add(attachment_link.attachment_id)
            attachments_synced += 1
        except Exception as e:
            logger.error(f"Failed to sync attachment {attachment.id} to QBO: {e}")
    
    return attachments_synced
