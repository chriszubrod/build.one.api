# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query

# Local Imports
from entities.vendor_compliance_document.api.schemas import (
    VendorFolderImportRequest,
    VendorFolderLinkRequest,
)
from entities.vendor_compliance_document.business.folder_service import VendorFolderService
from shared.api.responses import item_response, list_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "vendor-compliance-folder"])
folder_service = VendorFolderService()


@router.get("/vendor-compliance/folder/drives")
def list_vendor_compliance_folder_drives_router(
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    try:
        return list_response(folder_service.list_drives())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vendor-compliance/folder/browse")
def browse_vendor_compliance_folder_router(
    drive_public_id: str = Query(..., min_length=1),
    item_id: Optional[str] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    try:
        return list_response(folder_service.browse(drive_public_id, item_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vendor-compliance/{vendor_public_id}/folder")
def get_vendor_compliance_linked_folder_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    try:
        return item_response(folder_service.get_linked_folder(vendor_public_id))
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vendor-compliance/{vendor_public_id}/folder/link")
def link_vendor_compliance_folder_router(
    vendor_public_id: str,
    body: VendorFolderLinkRequest,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    try:
        return item_response(
            folder_service.link_folder(
                vendor_public_id,
                body.drive_public_id,
                body.graph_item_id,
            )
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/vendor-compliance/{vendor_public_id}/folder")
def unlink_vendor_compliance_folder_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete")),
):
    try:
        return item_response(folder_service.unlink_folder(vendor_public_id))
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower() or "no linked folder" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vendor-compliance/{vendor_public_id}/folder/files")
def list_vendor_compliance_folder_files_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    try:
        return list_response(folder_service.list_files(vendor_public_id))
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vendor-compliance/{vendor_public_id}/folder/import")
def import_vendor_compliance_folder_file_router(
    vendor_public_id: str,
    body: VendorFolderImportRequest,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    try:
        return item_response(
            folder_service.import_file(
                vendor_public_id=vendor_public_id,
                graph_item_id=body.graph_item_id,
                document_type=body.document_type,
                issuing_authority=body.issuing_authority,
                document_number=body.document_number,
                classification=body.classification,
                issue_date=body.issue_date,
                expiry_date=body.expiry_date,
            )
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
