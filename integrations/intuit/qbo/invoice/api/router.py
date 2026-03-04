# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.invoice.api.schemas import QboInvoiceSync
from integrations.intuit.qbo.invoice.business.service import QboInvoiceService
from entities.auth.business.service import get_current_user_api as get_current_qbo_invoice_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-invoice"])
service = QboInvoiceService()


@router.post("/sync/qbo-invoices")
def sync_qbo_invoices_router(body: QboInvoiceSync, current_user: dict = Depends(get_current_qbo_invoice_api)):
    """
    Sync Invoices from QBO.
    """
    invoices = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        customer_ref=body.customer_ref,
        sync_to_modules=body.sync_to_modules,
    )
    return [invoice.to_dict() for invoice in invoices]


@router.get("/get/qbo-invoices/realm/{realm_id}")
def get_qbo_invoices_by_realm_id_router(realm_id: str, current_user: dict = Depends(get_current_qbo_invoice_api)):
    """
    Read all QBO invoices by realm ID.
    """
    invoices = service.read_by_realm_id(realm_id=realm_id)
    return [invoice.to_dict() for invoice in invoices]


@router.get("/get/qbo-invoice/qbo-id/{qbo_id}")
def get_qbo_invoice_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(get_current_qbo_invoice_api)):
    """
    Read a QBO invoice by QBO ID.
    """
    invoice = service.read_by_qbo_id(qbo_id=qbo_id)
    return invoice.to_dict() if invoice else None


@router.get("/get/qbo-invoices")
def get_qbo_invoices_router(current_user: dict = Depends(get_current_qbo_invoice_api)):
    """
    Read all QBO invoices.
    """
    invoices = service.read_all()
    return [invoice.to_dict() for invoice in invoices]


@router.get("/get/qbo-invoice/{id}")
def get_qbo_invoice_by_id_router(id: int, current_user: dict = Depends(get_current_qbo_invoice_api)):
    """
    Read a QBO invoice by ID.
    """
    invoice = service.read_by_id(id=id)
    return invoice.to_dict() if invoice else None


@router.get("/get/qbo-invoice/{id}/lines")
def get_qbo_invoice_lines_router(id: int, current_user: dict = Depends(get_current_qbo_invoice_api)):
    """
    Read all QBO invoice lines for an invoice.
    """
    lines = service.read_lines_by_qbo_invoice_id(qbo_invoice_id=id)
    return [line.to_dict() for line in lines]
