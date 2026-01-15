# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.bill.api.schemas import QboBillSync
from integrations.intuit.qbo.bill.business.service import QboBillService
from modules.auth.business.service import get_current_user_api as get_current_qbo_bill_api

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
