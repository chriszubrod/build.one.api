# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.base.locking import qbo_sync_locked_route
from integrations.intuit.qbo.vendor.api.schemas import QboVendorSync
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response
from shared.authz.context import system_authz

# U-529: seven routes were DELETED from this module, not repaired —
# `POST /create/qbo-vendor`, `PUT /update/qbo-vendor/{id}`,
# `DELETE /delete/qbo-vendor/{id}` and the four
# `GET /get/qbo-vendor/{sync-token,display-name,company-name,tax-identifier}/…`
# reads. Every one called a `QboVendorService` method that has never existed
# (`create`, `update_by_id`, `delete_by_id`, `read_by_sync_token`,
# `read_by_display_name`, `read_by_company_name`, `read_by_tax_identifier`), so
# each raised `AttributeError` -> 500 on every call it ever received. They were
# generated alongside the pre-React Jinja templates described in
# `qbo.vendor.spec.md`; those templates are gone (Wave E5) and a sweep of all
# five umbrella repos — API (agent tools included), web, iOS, MCP, scheduler —
# found zero callers.
#
# Deleted rather than repaired, per the U-519 precedent: writing the missing
# service methods would turn a never-working endpoint into a LIVE, caller-keyed
# write primitive over `qbo.Vendor` — exactly the regression U-519 caught on
# `qbo.PhysicalAddress`. `qbo.Vendor` is populated by the QBO pull alone;
# nothing outside this integration may create, mutate or delete a staging row.
# The create/update pair also bound `bill_addr_id` straight off the request
# body, a column U-513 is dropping.
#
# The staging pull (`POST /sync/qbo-vendors`) and the four read routes below are
# the package's entire supported API surface.

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-vendor"])
service = QboVendorService()


@router.post("/sync/qbo-vendors")
@qbo_sync_locked_route("vendor")
def sync_qbo_vendors_router(body: QboVendorSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Sync Vendors from QBO.

    A QBO pull is a system-level operation spanning all users' rows. The
    connector resolves existing entities via UserProject/access-scoped lookups;
    under the requesting user's authz those reads can return None and drive
    duplicate creation / mapping deletion. Assert system intent at the boundary
    via the shared `system_authz()` contextmanager like the outbox worker /
    admin drain. See feedback_outbox_authz_boundary.md.
    """
    with system_authz():
        result = service.sync_from_qbo(
            realm_id=body.realm_id,
            last_updated_time=body.last_updated_time,
            sync_to_modules=body.sync_to_modules
        )
    return list_response([vendor.to_dict() for vendor in result.synced])


@router.get("/get/qbo-vendors/realm/{realm_id}")
def get_qbo_vendors_by_realm_id_router(realm_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO vendors by realm ID.
    """
    vendors = service.read_by_realm_id(realm_id=realm_id)
    return list_response([vendor.to_dict() for vendor in vendors])


@router.get("/get/qbo-vendor/qbo-id/{qbo_id}")
def get_qbo_vendor_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO vendor by QBO ID.
    """
    vendor = service.read_by_qbo_id(qbo_id=qbo_id)
    return vendor.to_dict() if vendor else None


@router.get("/get/qbo-vendors")
def get_qbo_vendors_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO vendors.
    """
    vendors = service.read_all()
    return list_response([vendor.to_dict() for vendor in vendors])


@router.get("/get/qbo-vendor/{id}")
def get_qbo_vendor_by_id_router(id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO vendor by ID.
    """
    vendor = service.read_by_id(id=id)
    return item_response(vendor.to_dict())
