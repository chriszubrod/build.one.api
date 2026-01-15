# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response

# Local Imports
from integrations.ms.sharepoint.driveitem.api.schemas import (
    DriveItemLinkRequest,
    FolderCreateRequest,
)
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from modules.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/ms/sharepoint/driveitem", tags=["api", "ms-sharepoint-driveitem"])


@router.get("/drive/{drive_public_id}/browse")
def browse_drive_root_router(
    drive_public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Browse items at the root of a linked drive.
    Returns items from MS Graph - does not store results.
    """
    service = MsDriveItemService()
    result = service.browse_drive_root(drive_public_id=drive_public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to browse drive root")
        )
    
    return result


@router.get("/drive/{drive_public_id}/browse/{item_id}")
def browse_folder_router(
    drive_public_id: str,
    item_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Browse items in a specific folder of a linked drive.
    Returns items from MS Graph - does not store results.
    """
    service = MsDriveItemService()
    result = service.browse_folder(drive_public_id=drive_public_id, item_id=item_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to browse folder")
        )
    
    return result


@router.get("/drive/{drive_public_id}/item/{item_id}")
def get_item_metadata_router(
    drive_public_id: str,
    item_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get metadata for a specific item from MS Graph.
    """
    service = MsDriveItemService()
    result = service.get_item_metadata(drive_public_id=drive_public_id, item_id=item_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to get item metadata")
        )
    
    return result


@router.get("/drive/{drive_public_id}/item/{item_id}/download")
def download_item_router(
    drive_public_id: str,
    item_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Download content of a file from MS Graph.
    """
    service = MsDriveItemService()
    result = service.download_item(drive_public_id=drive_public_id, item_id=item_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to download item")
        )
    
    content = result.get("content")
    content_type = result.get("content_type", "application/octet-stream")
    filename = result.get("filename", "download")
    
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.post("/drive/{drive_public_id}/item/{parent_item_id}/upload")
async def upload_file_router(
    drive_public_id: str,
    parent_item_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user_api)
):
    """
    Upload a file to a folder in MS Graph.
    Maximum file size: 4MB.
    """
    service = MsDriveItemService()
    
    # Read file content
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    filename = file.filename or "uploaded_file"
    
    result = service.upload_file(
        drive_public_id=drive_public_id,
        parent_item_id=parent_item_id,
        filename=filename,
        content=content,
        content_type=content_type
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to upload file")
        )
    
    return result


@router.post("/drive/{drive_public_id}/item/{parent_item_id}/folder")
def create_folder_router(
    drive_public_id: str,
    parent_item_id: str,
    body: FolderCreateRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Create a new folder in MS Graph.
    """
    service = MsDriveItemService()
    result = service.create_folder(
        drive_public_id=drive_public_id,
        parent_item_id=parent_item_id,
        folder_name=body.folder_name
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to create folder")
        )
    
    return result


@router.post("")
def link_item_router(
    body: DriveItemLinkRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Link a DriveItem by fetching from MS Graph and storing locally.
    """
    service = MsDriveItemService()
    result = service.link_item(
        drive_public_id=body.drive_public_id,
        item_id=body.item_id
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link item")
        )
    
    return result


@router.get("")
def list_linked_items_router(
    current_user: dict = Depends(get_current_user_api)
):
    """
    List all linked DriveItems.
    """
    service = MsDriveItemService()
    items = service.read_all()
    return {
        "message": f"Found {len(items)} linked items",
        "status_code": 200,
        "items": [item.to_dict() for item in items]
    }


@router.get("/drive/{drive_public_id}")
def list_linked_items_for_drive_router(
    drive_public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    List linked DriveItems for a specific drive.
    """
    service = MsDriveItemService()
    result = service.read_by_drive_public_id(drive_public_id=drive_public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to list items for drive")
        )
    
    return result


@router.get("/{public_id}")
def get_linked_item_router(
    public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get a linked DriveItem by public ID.
    """
    service = MsDriveItemService()
    item = service.read_by_public_id(public_id=public_id)
    
    if not item:
        raise HTTPException(
            status_code=404,
            detail="Linked item not found"
        )
    
    return {
        "message": "Item retrieved successfully",
        "status_code": 200,
        "item": item.to_dict()
    }


@router.post("/{public_id}/refresh")
def refresh_linked_item_router(
    public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Refresh a linked DriveItem by fetching latest data from MS Graph.
    """
    service = MsDriveItemService()
    result = service.refresh_item(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to refresh item")
        )
    
    return result


@router.delete("/{public_id}")
def unlink_item_router(
    public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Unlink a DriveItem by removing it from the database.
    """
    service = MsDriveItemService()
    result = service.unlink_item(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink item")
        )
    
    return result
