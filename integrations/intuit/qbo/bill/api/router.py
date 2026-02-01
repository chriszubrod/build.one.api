# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.bill.api.schemas import QboBillSync, QboBillPush
from integrations.intuit.qbo.bill.business.service import QboBillService
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.attachable.connector.attachment.business.service import AttachableAttachmentConnector
from services.bill.business.service import BillService
from services.bill_line_item.business.service import BillLineItemService
from services.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from services.attachment.business.service import AttachmentService
from services.auth.business.service import get_current_user_api as get_current_qbo_bill_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-bill"])
service = QboBillService()


@router.post("/sync/qbo-bills")
def sync_qbo_bills_router(body: QboBillSync, current_user: dict = Depends(get_current_qbo_bill_api)):
    """
    Sync Bills from QBO.
    """
    bills = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        sync_to_modules=body.sync_to_modules
    )
    return [bill.to_dict() for bill in bills]


@router.get("/get/qbo-bills/realm/{realm_id}")
def get_qbo_bills_by_realm_id_router(realm_id: str, current_user: dict = Depends(get_current_qbo_bill_api)):
    """
    Read all QBO bills by realm ID.
    """
    bills = service.read_by_realm_id(realm_id=realm_id)
    return [bill.to_dict() for bill in bills]


@router.get("/get/qbo-bill/qbo-id/{qbo_id}")
def get_qbo_bill_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(get_current_qbo_bill_api)):
    """
    Read a QBO bill by QBO ID.
    """
    bill = service.read_by_qbo_id(qbo_id=qbo_id)
    return bill.to_dict() if bill else None


@router.get("/get/qbo-bills")
def get_qbo_bills_router(current_user: dict = Depends(get_current_qbo_bill_api)):
    """
    Read all QBO bills.
    """
    bills = service.read_all()
    return [bill.to_dict() for bill in bills]


@router.get("/get/qbo-bill/{id}")
def get_qbo_bill_by_id_router(id: int, current_user: dict = Depends(get_current_qbo_bill_api)):
    """
    Read a QBO bill by ID.
    """
    bill = service.read_by_id(id=id)
    return bill.to_dict() if bill else None


@router.get("/get/qbo-bill/{id}/lines")
def get_qbo_bill_lines_router(id: int, current_user: dict = Depends(get_current_qbo_bill_api)):
    """
    Read all QBO bill lines for a bill.
    """
    lines = service.read_lines_by_qbo_bill_id(qbo_bill_id=id)
    return [line.to_dict() for line in lines]


@router.post("/sync/bill-to-qbo/{bill_public_id}")
def sync_bill_to_qbo_router(
    bill_public_id: str,
    body: QboBillPush,
    current_user: dict = Depends(get_current_qbo_bill_api)
):
    """
    Push a single local Bill to QuickBooks Online.
    
    This endpoint:
    1. Retrieves the local Bill by public_id
    2. Creates the Bill in QBO via the API
    3. Optionally syncs attachments to QBO
    4. Creates the BillBill mapping
    
    Args:
        bill_public_id: Public ID of the local Bill to push
        body: Request body with realm_id and sync options
        
    Returns:
        dict with status, qbo_bill info, and any errors
    """
    bill_service = BillService()
    bill_connector = BillBillConnector()
    
    # Get the bill
    bill = bill_service.read_by_public_id(public_id=bill_public_id)
    if not bill:
        raise HTTPException(status_code=404, detail=f"Bill with public_id '{bill_public_id}' not found")
    
    # Check if bill is finalized
    if bill.is_draft:
        raise HTTPException(
            status_code=400, 
            detail="Bill must be finalized (is_draft=False) before syncing to QBO"
        )
    
    try:
        # Push bill to QBO
        qbo_bill = bill_connector.sync_to_qbo_bill(bill=bill, realm_id=body.realm_id)
        
        result = {
            "success": True,
            "message": f"Bill pushed to QBO successfully",
            "bill_id": bill.id,
            "bill_public_id": bill.public_id,
            "bill_number": bill.bill_number,
            "qbo_bill_id": qbo_bill.qbo_id,
            "qbo_bill_local_id": qbo_bill.id,
            "attachments_synced": 0,
            "errors": [],
        }
        
        # Sync attachments if requested
        if body.sync_attachments and qbo_bill.qbo_id:
            try:
                attachment_count = _sync_bill_attachments(
                    bill=bill,
                    qbo_bill_id=qbo_bill.qbo_id,
                    realm_id=body.realm_id,
                )
                result["attachments_synced"] = attachment_count
            except Exception as att_e:
                logger.error(f"Failed to sync attachments for Bill {bill.id}: {att_e}")
                result["errors"].append(f"Attachment sync failed: {str(att_e)}")
        
        return result
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error pushing Bill {bill_public_id} to QBO")
        raise HTTPException(status_code=500, detail=f"Failed to push bill to QBO: {str(e)}")


def _sync_bill_attachments(bill, qbo_bill_id: str, realm_id: str) -> int:
    """
    Sync all attachments for a Bill to QBO.
    
    Returns the number of attachments synced.
    """
    bill_id = int(bill.id) if isinstance(bill.id, str) else bill.id
    
    bill_line_item_service = BillLineItemService()
    bill_line_item_attachment_service = BillLineItemAttachmentService()
    attachment_service = AttachmentService()
    attachment_connector = AttachableAttachmentConnector()
    
    # Get all line items for this bill
    line_items = bill_line_item_service.read_by_bill_id(bill_id=bill_id)
    if not line_items:
        return 0
    
    attachments_synced = 0
    synced_attachment_ids = set()
    
    for line_item in line_items:
        if not line_item.public_id:
            continue
        
        # Get attachment for this line item
        attachment_link = bill_line_item_attachment_service.read_by_bill_line_item_id(
            bill_line_item_public_id=line_item.public_id
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
                entity_type="Bill",
                entity_id=qbo_bill_id,
            )
            synced_attachment_ids.add(attachment_link.attachment_id)
            attachments_synced += 1
        except Exception as e:
            logger.error(f"Failed to sync attachment {attachment.id} to QBO: {e}")
    
    return attachments_synced
