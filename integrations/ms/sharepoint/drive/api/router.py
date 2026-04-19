# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.ms.sharepoint.drive.api.schemas import (
    DriveLinkRequest,
    DriveUpdateRequest,
    DriveCompanyLinkRequest,
)
from integrations.ms.sharepoint.drive.business.service import MsDriveService
from integrations.ms.sharepoint.drive.connector.company.business.service import DriveCompanyConnector
from integrations.ms.sharepoint.external.client import get_my_drive
from shared.api.responses import item_response, list_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/ms/sharepoint/drive", tags=["api", "ms-sharepoint-drive"])


@router.get("/me")
def get_my_drive_router(
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    Get the current user's OneDrive.
    Uses user-delegated authentication.
    """
    result = get_my_drive()
    return result


@router.get("/site/{site_public_id}/available")
def list_available_drives_router(
    site_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    List available drives from MS Graph for a linked site.
    Returns drives from the site - does not store results.
    """
    service = MsDriveService()
    result = service.list_available_drives(site_public_id=site_public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to list available drives")
        )
    
    return result


@router.post("")
def link_drive_router(
    body: DriveLinkRequest,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))
):
    """
    Link a drive by fetching from MS Graph and storing locally.
    """
    service = MsDriveService()
    result = service.link_drive(
        site_public_id=body.site_public_id,
        drive_id=body.drive_id
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link drive")
        )
    
    return result


@router.get("")
def list_linked_drives_router(
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    List all linked drives.
    """
    service = MsDriveService()
    drives = service.read_all()
    return list_response([drive.to_dict() for drive in drives])


@router.get("/site/{site_public_id}")
def list_linked_drives_for_site_router(
    site_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    List linked drives for a specific site.
    """
    service = MsDriveService()
    result = service.read_by_site_public_id(site_public_id=site_public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to list drives for site")
        )
    
    return result


@router.get("/{public_id}")
def get_linked_drive_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    Get a linked drive by public ID.
    """
    service = MsDriveService()
    drive = service.read_by_public_id(public_id=public_id)
    
    if not drive:
        raise_not_found("Drive")

    return item_response(drive.to_dict())


@router.put("/{public_id}")
def update_linked_drive_router(
    public_id: str,
    body: DriveUpdateRequest,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_update"))
):
    """
    Update a linked drive's name.
    """
    service = MsDriveService()
    result = service.update_by_public_id(
        public_id=public_id,
        name=body.name
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to update drive")
        )
    
    return result


@router.delete("/{public_id}")
def unlink_drive_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_delete"))
):
    """
    Unlink a drive by removing it from the database.
    """
    service = MsDriveService()
    result = service.unlink_drive(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink drive")
        )
    
    return result


@router.post("/{public_id}/refresh")
def refresh_linked_drive_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    Refresh a linked drive by fetching latest data from MS Graph.
    """
    service = MsDriveService()
    result = service.refresh_drive(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to refresh drive")
        )
    
    return result


# =============================================================================
# Drive-Company Connector Endpoints
# =============================================================================


@router.post("/connector/company")
def link_drive_to_company_router(
    body: DriveCompanyLinkRequest,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))
):
    """
    Link a Drive (OneDrive or SharePoint) to a Company.
    This will:
    1. Store the Site in ms.Site (if not already stored)
    2. Store the Drive in ms.Drive (if not already stored)
    3. Create the mapping in ms.DriveCompany
    """
    connector = DriveCompanyConnector()
    result = connector.link_drive_to_company(
        company_id=body.company_id,
        graph_site_id=body.graph_site_id,
        graph_drive_id=body.graph_drive_id
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link drive to company")
        )
    
    return result


@router.get("/connector/company/{company_id}")
def get_drive_for_company_router(
    company_id: int,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    Get the linked Drive for a Company.
    """
    connector = DriveCompanyConnector()
    mapping = connector.get_drive_for_company(company_id=company_id)
    
    if not mapping:
        raise_not_found("Drive mapping")

    return item_response(mapping.to_dict())


@router.delete("/connector/company/{company_id}")
def unlink_drive_from_company_router(
    company_id: int,
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_delete"))
):
    """
    Unlink the Drive from a Company.
    """
    connector = DriveCompanyConnector()
    result = connector.unlink_by_company_id(company_id=company_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink drive from company")
        )
    
    return result
