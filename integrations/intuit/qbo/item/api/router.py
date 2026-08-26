# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.item.api.schemas import QboItemSync
from integrations.intuit.qbo.item.business.service import QboItemService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-item"])
service = QboItemService()


@router.post("/sync/qbo-items")
def sync_qbo_items_router(body: QboItemSync, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Sync Items from QBO.
    """
    result = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        sync_to_modules=body.sync_to_modules
    )
    return list_response([item.to_dict() for item in result.synced])

# U-307d: the 3 admin GET routes (/get/qbo-items, /get/qbo-items/realm/{realm_id},
# /get/qbo-item/{qbo_id}) were removed — they read qbo.Item directly (zero web callers)
# and would 500 once U-307d drops the table. POST /sync/qbo-items (the live pull) stays;
# it drives the dbo-native ItemCostCode/ItemSubCostCode connectors, no qbo.Item read.

