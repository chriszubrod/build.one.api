# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.sync.api.schemas import SyncCreate, SyncUpdate
from integrations.sync.business.service import SyncService
from entities.auth.business.service import get_current_user_api as get_current_sync_api

router = APIRouter(prefix="/api/v1", tags=["api", "sync"])
service = SyncService()


@router.post("/create/sync")
def create_sync_router(body: SyncCreate, current_user: dict = Depends(get_current_sync_api)):
    """
    Create a new sync record.
    """
    sync = service.create(
        provider=body.provider,
        env=body.env,
        entity=body.entity,
        last_sync_datetime=body.last_sync_datetime,
    )
    return sync.to_dict()


@router.get("/get/syncs")
def get_syncs_router(current_user: dict = Depends(get_current_sync_api)):
    """
    Read all sync records.
    """
    syncs = service.read_all()
    return [sync.to_dict() for sync in syncs]


@router.get("/get/sync/{public_id}")
def get_sync_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_sync_api)):
    """
    Read a sync record by public ID.
    """
    sync = service.read_by_public_id(public_id=public_id)
    return sync.to_dict()


@router.put("/update/sync/{public_id}")
def update_sync_by_public_id_router(public_id: str, body: SyncUpdate, current_user: dict = Depends(get_current_sync_api)):
    """
    Update a sync record by public ID.
    """
    sync = service.update_by_public_id(public_id=public_id, sync=body)
    return sync.to_dict()


@router.delete("/delete/sync/{public_id}")
def delete_sync_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_sync_api)):
    """
    Delete a sync record by public ID.
    """
    sync = service.delete_by_public_id(public_id=public_id)
    return sync.to_dict()
