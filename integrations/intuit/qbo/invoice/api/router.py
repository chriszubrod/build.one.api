# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.invoice.api.schemas import QboInvoiceSync
from integrations.intuit.qbo.invoice.business.service import QboInvoiceService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)
from shared.api.responses import list_response, item_response

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-invoice"])
service = QboInvoiceService()


@router.post("/sync/qbo-invoices")
def sync_qbo_invoices_router(body: QboInvoiceSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Invoice QBO sync is disabled. Invoices are managed manually in QBO.
    """
    # QBO invoice sync disabled — invoices are managed manually in QBO
    logger.info("Invoice QBO pull sync disabled; skipping sync_from_qbo")
    return []


@router.get("/get/qbo-invoices/realm/{realm_id}")
def get_qbo_invoices_by_realm_id_router(realm_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO invoices by realm ID.
    """
    invoices = service.read_by_realm_id(realm_id=realm_id)
    return list_response([invoice.to_dict() for invoice in invoices])


@router.get("/get/qbo-invoice/qbo-id/{qbo_id}")
def get_qbo_invoice_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO invoice by QBO ID.
    """
    invoice = service.read_by_qbo_id(qbo_id=qbo_id)
    return invoice.to_dict() if invoice else None


@router.get("/get/qbo-invoices")
def get_qbo_invoices_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO invoices.
    """
    invoices = service.read_all()
    return list_response([invoice.to_dict() for invoice in invoices])


@router.get("/get/qbo-invoice/{id}")
def get_qbo_invoice_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO invoice by ID.
    """
    invoice = service.read_by_id(id=id)
    return invoice.to_dict() if invoice else None


@router.get("/get/qbo-invoice/{id}/lines")
def get_qbo_invoice_lines_router(id: int, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO invoice lines for an invoice.
    """
    lines = service.read_lines_by_qbo_invoice_id(qbo_invoice_id=id)
    return list_response([line.to_dict() for line in lines])
