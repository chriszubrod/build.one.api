# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.base.locking import qbo_sync_locked_route
from integrations.intuit.qbo.customer.api.schemas import QboCustomerSync
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response
from shared.authz.context import system_authz

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-customer"])
service = QboCustomerService()


@router.post("/sync/qbo-customers")
@qbo_sync_locked_route("customer")
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
    with system_authz():
        result = service.sync_from_qbo(
            realm_id=body.realm_id,
            last_updated_time=body.last_updated_time,
            sync_to_modules=body.sync_to_modules
        )
    return list_response([customer.to_dict() for customer in result.synced])

# U-348: the 3 admin GET routes (/get/qbo-customers, /get/qbo-customers/realm/{realm_id},
# /get/qbo-customer/{qbo_id}) were removed when this router was mounted — they read
# qbo.Customer directly (zero web/scheduler/api callers) and qbo.Customer is a Wave-5
# "trust-dbo" staging-removal drop target, so they would 500 once the table drops.
# Mirrors U-307d's identical treatment of the item router. POST /sync/qbo-customers
# (the live pull) stays; it drives the dbo-native Customer/Project + QboId identity.
