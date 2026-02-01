# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.item.api.schemas import QboItemSync
from integrations.intuit.qbo.item.business.service import QboItemService
from services.auth.business.service import get_current_user_api as get_current_qbo_item_api

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-item"])
service = QboItemService()


@router.post("/sync/qbo-items")
def sync_qbo_items_router(body: QboItemSync, current_user: dict = Depends(get_current_qbo_item_api)):
    """
    Sync Items from QBO.
    """
    items = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        sync_to_modules=body.sync_to_modules
    )
    return [item.to_dict() for item in items]


@router.get("/get/qbo-items")
def get_qbo_items_router(current_user: dict = Depends(get_current_qbo_item_api)):
    """
    Read all QBO items.
    """
    items = service.read_all()
    return [item.to_dict() for item in items]


@router.get("/get/qbo-items/realm/{realm_id}")
def get_qbo_items_by_realm_id_router(realm_id: str, current_user: dict = Depends(get_current_qbo_item_api)):
    """
    Read all QBO items by realm ID.
    """
    items = service.read_by_realm_id(realm_id=realm_id)
    return [item.to_dict() for item in items]


@router.get("/get/qbo-item/{qbo_id}")
def get_qbo_item_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(get_current_qbo_item_api)):
    """
    Read a QBO item by QBO ID.
    """
    item = service.read_by_qbo_id(qbo_id=qbo_id)
    return item.to_dict() if item else None

