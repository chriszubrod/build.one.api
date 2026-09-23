# Python Standard Library Imports
import base64

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.physical_address.api.schemas import (
    QboPhysicalAddressCreate,
    QboPhysicalAddressUpdate,
)
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-physical-address"])
service = QboPhysicalAddressService()


@router.post("/intuit/qbo/physical-address/create")
def create_qbo_physical_address_router(body: QboPhysicalAddressCreate, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Create a new QBO physical address.
    """
    address = service.create(
        qbo_id=body.qbo_id,
        line1=body.line1,
        line2=body.line2,
        city=body.city,
        country=body.country,
        country_sub_division_code=body.country_sub_division_code,
        postal_code=body.postal_code,
    )
    return item_response(address.to_dict())


@router.get("/intuit/qbo/physical-address/list")
def list_qbo_physical_addresses_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read all QBO physical addresses.
    """
    addresses = service.read_all()
    return list_response([address.to_dict() for address in addresses])


@router.get("/intuit/qbo/physical-address/read/{id}")
def read_qbo_physical_address_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Read a QBO physical address by ID.
    """
    address = service.read_by_id(id=id)
    if not address:
        raise HTTPException(status_code=404, detail="Physical address not found")
    return item_response(address.to_dict())


@router.put("/intuit/qbo/physical-address/update/{id}")
def update_qbo_physical_address_by_id_router(id: int, body: QboPhysicalAddressUpdate, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_update"))):
    """
    Update a QBO physical address by ID.
    """
    address = service.update_by_id(
        id=id,
        row_version=base64.b64decode(body.row_version),
        qbo_id=body.qbo_id,
        line1=body.line1,
        line2=body.line2,
        city=body.city,
        country=body.country,
        country_sub_division_code=body.country_sub_division_code,
        postal_code=body.postal_code,
    )
    if not address:
        raise HTTPException(status_code=404, detail="Physical address not found")
    return item_response(address.to_dict())


@router.delete("/intuit/qbo/physical-address/delete/{id}")
def delete_qbo_physical_address_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_delete"))):
    """
    Delete a QBO physical address by ID.
    """
    address = service.delete_by_id(id=id)
    if not address:
        raise HTTPException(status_code=404, detail="Physical address not found")
    return item_response(address.to_dict())


# U-519 fix round 2 — `POST /intuit/qbo/physical-address/sync` DELETED, not repaired.
#
# The route read `body.qbo_id` while its schema declared `address_id`, so pydantic
# raised AttributeError and EVERY call 500'd from its first commit (144b3a30) to
# this one. Round 1 "fixed" that by reading `body.address_id` — which made a
# caller-keyed, unscoped upsert reachable for the first time:
#
#   * `QboPhysicalAddressClient.get_physical_address(qbo_id)` IGNORES the id
#     ("Unused; kept for API consistency") and always returns the realm's own
#     CompanyInfo address, so `address_id` was never a remote selector.
#   * It was purely the LOCAL upsert key: `record_id = qbo_id or realm_id` ->
#     `read_by_qbo_id(record_id)`, whose sproc is `WHERE [QboId] = @QboId` with
#     no realm and no ownership predicate.
#   * The keyspace is guessable — `{customer.id}_bill` / `_ship` / `{vendor.id}_bill`.
#
#   So one authenticated QBO_SYNC/can_create call with `address_id="1246_bill"`
#   overwrote that party's staged address with the company's own and re-stamped
#   its RealmId. `require_module_api` gates WHO may sync, never WHICH row they key.
#   Blast radius left staging: qbo.PhysicalAddress -> sync_from_qbo_to_address ->
#   dbo.Address -> the ProjectAddress row U-506 P0 renders as the mailed
#   "TO OWNER:" block on every draw-request packet.
#
# Deleted rather than narrowed because the route has ZERO callers anywhere in the
# umbrella (api/web/ios/mcp/scheduler) and has never once returned a success
# response, so there is no contract to preserve. `qbo.PhysicalAddress` is itself
# the sequenced endpoint of the U-506 staging sunset. Guarded by
# tests/test_u519_physical_address_live_bugs.py, which fails if any route in this
# package reaches sync_from_qbo with a caller-supplied record key.

