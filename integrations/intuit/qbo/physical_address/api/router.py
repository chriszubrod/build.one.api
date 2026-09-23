# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-physical-address"])
service = QboPhysicalAddressService()


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


# ── U-519 fix round 3: the WHOLE write surface is deleted, not just /sync ─────
#
# Round 2 deleted /sync as a caller-keyed unscoped write. It MISSED that the
# same commit made `PUT /update/{id}` live for the first time, via this unit's
# own base64 fix -- the identical defect on a STRICTLY MORE POWERFUL primitive,
# found by an adversarial review of the pushed commit:
#
#   * The PUT had passed a base64 STRING into `@RowVersion BINARY(8)` since
#     144b3a30, so it matched 0 rows and 500'd on every call. Decoding to bytes
#     (correct in itself, and required for the sync path) made it functional.
#   * `UpdateQboPhysicalAddressById` is `WHERE [Id] = @Id AND [RowVersion] =
#     @RowVersion` -- no realm, no owner -- and it SETS [QboId] and [RealmId]
#     from parameters. So a caller could rewrite any staging row's CONTENT *and*
#     re-stamp its IDENTITY onto another party's.
#   * row_version is not a defence: `GET /read/{id}` hands it to the same role.
#   * Worse than /sync on reach: Controller is seeded ('QBO Sync', 0,1,1,0,...)
#     = can_read + can_update but NOT can_create, so /sync was never reachable
#     by a Controller and this PUT was the one write on the package that was.
#
# POST /create and DELETE /delete/{id} go with it: same unscoped staging writes,
# same caller-settable QboId, same zero callers. Verified zero consumers across
# api/web/ios/mcp/scheduler INCLUDING `entities/*/intelligence/tools.py`, which a
# plain route grep misses. qbo.PhysicalAddress is written by the PULL path only,
# and is itself the U-513 sunset target.
#
# The two READ routes remain. They are unscoped by realm (see U-514) but they
# disclose addresses rather than mutate identity.
#
# U-519 round 2 note, retained: — `POST /intuit/qbo/physical-address/sync` DELETED, not repaired.
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

