# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

# Local Imports
from integrations.intuit.qbo.bill.api.schemas import QboBillSync, QboBillPush
from integrations.intuit.qbo.bill.business.service import QboBillService
from integrations.intuit.qbo.outbox.business.service import QboOutboxService
from entities.bill.business.service import BillService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)
from shared.api.responses import list_response, item_response

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-bill"])
service = QboBillService()


@router.post("/sync/qbo-bills")
def sync_qbo_bills_router(body: QboBillSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Sync Bills from QBO.
    """
    result = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        sync_to_modules=body.sync_to_modules
    )
    return list_response([bill.to_dict() for bill in result.synced])


@router.get("/get/qbo-bills/realm/{realm_id}")
def get_qbo_bills_by_realm_id_router(realm_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO bills by realm ID.
    """
    bills = service.read_by_realm_id(realm_id=realm_id)
    return list_response([bill.to_dict() for bill in bills])


@router.get("/get/qbo-bill/qbo-id/{qbo_id}")
def get_qbo_bill_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO bill by QBO ID.
    """
    bill = service.read_by_qbo_id(qbo_id=qbo_id)
    return bill.to_dict() if bill else None


@router.get("/get/qbo-bills")
def get_qbo_bills_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO bills.
    """
    bills = service.read_all()
    return list_response([bill.to_dict() for bill in bills])


@router.get("/get/qbo-bill/{id}")
def get_qbo_bill_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO bill by ID.
    """
    bill = service.read_by_id(id=id)
    return bill.to_dict() if bill else None


@router.get("/get/qbo-bill/{id}/lines")
def get_qbo_bill_lines_router(id: int, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO bill lines for a bill.
    """
    lines = service.read_lines_by_qbo_bill_id(qbo_bill_id=id)
    return list_response([line.to_dict() for line in lines])


@router.post(
    "/sync/bill-to-qbo/{bill_public_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_bill_to_qbo_router(
    bill_public_id: str,
    body: QboBillPush,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))
):
    """
    Queue a single local Bill for async push to QuickBooks Online via the outbox.

    The outbox worker drains the row and calls BillBillConnector.sync_to_qbo_bill
    with a durable RequestId for Intuit dedup. Returns immediately with 202.
    """
    bill_service = BillService()

    bill = bill_service.read_by_public_id(public_id=bill_public_id)
    if not bill:
        raise HTTPException(status_code=404, detail=f"Bill with public_id '{bill_public_id}' not found")

    if bill.is_draft:
        raise HTTPException(
            status_code=400,
            detail="Bill must be finalized (is_draft=False) before syncing to QBO"
        )

    outbox_row = QboOutboxService().enqueue(
        kind="sync_bill_to_qbo",
        entity_type="Bill",
        entity_public_id=str(bill.public_id),
        realm_id=body.realm_id,
    )
    logger.info(
        f"Enqueued QBO sync for Bill {bill.public_id} (outbox {outbox_row.public_id})"
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"outbox_public_id": outbox_row.public_id},
    )
