# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response

# Local Imports
from integrations.ms.sharepoint.driveitem.api.schemas import (
    DriveItemLinkRequest,
    FolderCreateRequest,
    DriveItemProjectLinkRequest,
    DriveItemProjectModuleLinkRequest,
    DriveItemProjectExcelLinkRequest,
    DriveItemProjectExcelPushDataRequest,
    DriveItemProjectExcelAppendRowsRequest,
    DriveItemProjectExcelClearRangeRequest,
)
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.connector.project.business.service import DriveItemProjectConnector
from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import DriveItemProjectExcelConnector
from services.auth.business.service import get_current_user_api

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


# =============================================================================
# DriveItem-Project Connector Endpoints
# =============================================================================


@router.post("/connector/project")
def link_driveitem_to_project_router(
    body: DriveItemProjectLinkRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Link a DriveItem (folder) to a Project.
    This will:
    1. Store the DriveItem in ms.DriveItem (if not already stored)
    2. Create the mapping in ms.DriveItemProject
    """
    connector = DriveItemProjectConnector()
    result = connector.link_driveitem_to_project(
        project_id=body.project_id,
        drive_public_id=body.drive_public_id,
        graph_item_id=body.graph_item_id
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link folder to project")
        )
    
    return result


@router.get("/connector/project/{project_id}")
def get_driveitem_for_project_router(
    project_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get the linked DriveItem (folder) for a Project.
    """
    connector = DriveItemProjectConnector()
    driveitem = connector.get_driveitem_for_project(project_id=project_id)
    
    if not driveitem:
        return {
            "message": "No linked folder found for this project",
            "status_code": 404,
            "driveitem": None
        }
    
    return {
        "message": "Folder mapping retrieved successfully",
        "status_code": 200,
        "driveitem": driveitem
    }


@router.delete("/connector/project/{project_id}")
def unlink_driveitem_from_project_router(
    project_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Unlink the DriveItem (folder) from a Project.
    """
    connector = DriveItemProjectConnector()
    result = connector.unlink_by_project_id(project_id=project_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink folder from project")
        )
    
    return result


# =============================================================================
# DriveItem-ProjectModule Connector Endpoints
# =============================================================================


@router.post("/connector/project-module")
def link_module_folder_to_project_router(
    body: DriveItemProjectModuleLinkRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Link a DriveItem (folder) to a Project Module.
    This will:
    1. Verify the project has a root folder
    2. Verify the selected folder is a child of the project root
    3. Store the DriveItem in ms.DriveItem (if not already stored)
    4. Create the mapping in ms.DriveItemProjectModule
    """
    connector = DriveItemProjectModuleConnector()
    result = connector.link_module_folder(
        project_id=body.project_id,
        module_id=body.module_id,
        graph_item_id=body.graph_item_id
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link folder to project module")
        )
    
    return result


@router.get("/connector/project-module/{project_id}/{module_id}")
def get_module_folder_for_project_router(
    project_id: int,
    module_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get the linked DriveItem (folder) for a specific module in a Project.
    """
    connector = DriveItemProjectModuleConnector()
    driveitem = connector.get_folder_for_module(project_id=project_id, module_id=module_id)
    
    if not driveitem:
        return {
            "message": f"No linked folder found for module ID {module_id} in this project",
            "status_code": 404,
            "driveitem": None
        }
    
    return {
        "message": "Folder mapping retrieved successfully",
        "status_code": 200,
        "driveitem": driveitem
    }


@router.get("/connector/project-module/{project_id}")
def get_all_module_folders_for_project_router(
    project_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get all linked module folders for a Project.
    """
    connector = DriveItemProjectModuleConnector()
    folders = connector.get_all_module_folders(project_id=project_id)
    
    return {
        "message": f"Found {len(folders)} linked module folders",
        "status_code": 200,
        "folders": folders
    }


@router.delete("/connector/project-module/{project_id}/{module_id}")
def unlink_module_folder_from_project_router(
    project_id: int,
    module_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Unlink the DriveItem (folder) from a Project Module.
    """
    connector = DriveItemProjectModuleConnector()
    result = connector.unlink_module_folder(project_id=project_id, module_id=module_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink folder from project module")
        )
    
    return result


# =============================================================================
# DriveItem-ProjectExcel Connector Endpoints
# =============================================================================


@router.post("/connector/project-excel")
def link_excel_to_project_router(
    body: DriveItemProjectExcelLinkRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Link an Excel workbook to a Project.
    This will:
    1. Store the DriveItem in ms.DriveItem (if not already stored)
    2. Validate the file is an Excel workbook (.xlsx)
    3. Validate the worksheet exists in the workbook
    4. Create the mapping in ms.DriveItemProjectExcel
    """
    connector = DriveItemProjectExcelConnector()
    result = connector.link_excel_to_project(
        project_id=body.project_id,
        drive_public_id=body.drive_public_id,
        graph_item_id=body.graph_item_id,
        worksheet_name=body.worksheet_name
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link Excel workbook to project")
        )
    
    return result


@router.get("/connector/project-excel/{project_id}")
def get_excel_for_project_router(
    project_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get the linked Excel workbook for a Project.
    """
    connector = DriveItemProjectExcelConnector()
    excel = connector.get_excel_for_project(project_id=project_id)
    
    if not excel:
        return {
            "message": "No linked Excel workbook found for this project",
            "status_code": 404,
            "excel": None
        }
    
    return {
        "message": "Excel workbook mapping retrieved successfully",
        "status_code": 200,
        "excel": excel
    }


@router.delete("/connector/project-excel/{project_id}")
def unlink_excel_from_project_router(
    project_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Unlink the Excel workbook from a Project.
    """
    connector = DriveItemProjectExcelConnector()
    result = connector.unlink_excel_from_project(project_id=project_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink Excel workbook from project")
        )
    
    return result


@router.get("/connector/project-excel/{project_id}/worksheets")
def list_worksheets_router(
    project_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    List all worksheets in the linked Excel workbook.
    """
    connector = DriveItemProjectExcelConnector()
    result = connector.list_worksheets(project_id=project_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to list worksheets")
        )
    
    return result


@router.get("/drive/{drive_public_id}/item/{item_id}/worksheets")
def get_workbook_worksheets_router(
    drive_public_id: str,
    item_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get list of worksheets in an Excel workbook (does not require workbook to be linked).
    """
    from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
    from integrations.ms.sharepoint.external.client import get_excel_worksheets
    
    drive_repo = MsDriveRepository()
    drive = drive_repo.read_by_public_id(drive_public_id)
    
    if not drive:
        raise HTTPException(
            status_code=404,
            detail="Drive not found"
        )
    
    result = get_excel_worksheets(drive.drive_id, item_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to get worksheets")
        )
    
    return result


@router.post("/connector/project-excel/{project_id}/push-data")
def push_data_to_worksheet_router(
    project_id: int,
    body: DriveItemProjectExcelPushDataRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Push data to the stored worksheet in the linked Excel workbook.
    """
    connector = DriveItemProjectExcelConnector()
    result = connector.push_data_to_worksheet(
        project_id=project_id,
        data=body.data,
        range_address=body.range_address
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to push data to worksheet")
        )
    
    return result


@router.post("/connector/project-excel/{project_id}/append-rows")
def append_rows_to_worksheet_router(
    project_id: int,
    body: DriveItemProjectExcelAppendRowsRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Append rows to the stored worksheet in the linked Excel workbook.
    """
    connector = DriveItemProjectExcelConnector()
    result = connector.append_rows_to_worksheet(
        project_id=project_id,
        rows=body.rows
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to append rows to worksheet")
        )
    
    return result


@router.delete("/connector/project-excel/{project_id}/clear-range")
def clear_worksheet_range_router(
    project_id: int,
    body: DriveItemProjectExcelClearRangeRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Clear a range in the stored worksheet in the linked Excel workbook.
    """
    connector = DriveItemProjectExcelConnector()
    result = connector.clear_worksheet_range(
        project_id=project_id,
        range_address=body.range_address
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to clear worksheet range")
        )
    
    return result
