# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.customer.api.schemas import QboCustomerSync
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-customer"])
service = QboCustomerService()


@router.post("/sync/qbo-customers")
def sync_qbo_customers_router(body: QboCustomerSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Sync Customers from QBO.
    """
    customers = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        sync_to_modules=body.sync_to_modules
    )
    return [customer.to_dict() for customer in customers]


@router.get("/get/qbo-customers")
def get_qbo_customers_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO customers.
    """
    customers = service.read_all()
    return [customer.to_dict() for customer in customers]


@router.get("/get/qbo-customers/realm/{realm_id}")
def get_qbo_customers_by_realm_id_router(realm_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO customers by realm ID.
    """
    customers = service.read_by_realm_id(realm_id=realm_id)
    return [customer.to_dict() for customer in customers]


@router.get("/get/qbo-customer/{qbo_id}")
def get_qbo_customer_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO customer by QBO ID.
    """
    customer = service.read_by_qbo_id(qbo_id=qbo_id)
    return customer.to_dict() if customer else None
