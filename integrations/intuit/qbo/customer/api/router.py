# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from integrations.intuit.qbo.base.locking import qbo_app_lock, qbo_entity_sync_lock_resource
from integrations.intuit.qbo.customer.api.schemas import QboCustomerSync
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response
from shared.authz.context import system_authz

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-customer"])
service = QboCustomerService()


@router.post("/sync/qbo-customers")
def sync_qbo_customers_router(body: QboCustomerSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Sync Customers from QBO.

    A QBO pull is a system-level operation that touches rows across all users
    (Customers, and the Projects derived from job/sub-customers). The connector
    reads existing Projects via UserProject-scoped lookups; under the requesting
    user's authz those reads return None for projects the caller can't see, which
    made the connector delete valid mappings and create duplicate Projects. Assert
    system intent at the boundary via the shared `system_authz()` contextmanager
    exactly like the outbox worker / admin drain, so scoped reads see every row.
    See feedback_outbox_authz_boundary.md.
    """
    lock_resource = qbo_entity_sync_lock_resource("customer")
    with qbo_app_lock(lock_resource) as got_lock:
        if not got_lock:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"QBO customer sync already in progress for realm {body.realm_id}. Try again shortly.",
            )
        with system_authz():
            result = service.sync_from_qbo(
                realm_id=body.realm_id,
                last_updated_time=body.last_updated_time,
                sync_to_modules=body.sync_to_modules
            )
    return list_response([customer.to_dict() for customer in result.synced])


@router.get("/get/qbo-customers")
def get_qbo_customers_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO customers.
    """
    customers = service.read_all()
    return list_response([customer.to_dict() for customer in customers])


@router.get("/get/qbo-customers/realm/{realm_id}")
def get_qbo_customers_by_realm_id_router(realm_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO customers by realm ID.
    """
    customers = service.read_by_realm_id(realm_id=realm_id)
    return list_response([customer.to_dict() for customer in customers])


@router.get("/get/qbo-customer/{qbo_id}")
def get_qbo_customer_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO customer by QBO ID.
    """
    customer = service.read_by_qbo_id(qbo_id=qbo_id)
    return customer.to_dict() if customer else None
