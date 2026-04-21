# Python Standard Library Imports
import logging
import uuid
from typing import Any, Dict, Optional

# Local Imports
from core.workflow.business.pending_actions import (
    ACTION_BACKFILL_W9,
    ACTION_UPDATE_TAXPAYER,
)

logger = logging.getLogger(__name__)

# W9-like file name patterns and allowed extensions for backfill
W9_NAME_SUBSTRINGS = ("w9", "w-9")
W9_EXTENSIONS = ("pdf",)  # single-element tuple


def execute_pending_action(action_type: str, payload: Dict[str, Any], decision: str) -> Dict[str, Any]:
    """
    Execute or reject a pending action.

    Args:
        action_type: One of ACTION_UPDATE_TAXPAYER, ACTION_BACKFILL_W9, etc.
        payload: The action payload (e.g. taxpayer_public_id, row_version, ...).
        decision: "approve" or "reject".

    Returns:
        {"success": bool, "executed": bool, "result": Any, "error": str|None}
    """
    if decision != "approve":
        return {"success": True, "executed": False, "result": None, "error": None}

    if action_type == ACTION_UPDATE_TAXPAYER:
        return _execute_update_taxpayer(payload)
    if action_type == ACTION_BACKFILL_W9:
        return _execute_backfill_w9(payload)

    return {"success": False, "executed": False, "result": None, "error": f"Unknown action type: {action_type}"}


def _execute_update_taxpayer(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply approved update_taxpayer: update Taxpayer with W9-extracted fields."""
    from entities.taxpayer.business.service import TaxpayerService

    taxpayer_public_id = payload.get("taxpayer_public_id")
    row_version = payload.get("row_version")
    if not taxpayer_public_id or not row_version:
        return {"success": False, "executed": False, "result": None, "error": "Missing taxpayer_public_id or row_version"}

    try:
        service = TaxpayerService()
        # Only pass fields that are safe; classification from W9 is often free text, skip unless valid enum
        updated = service.update_by_public_id(
            public_id=taxpayer_public_id,
            row_version=row_version,
            taxpayer_id_number=payload.get("taxpayer_id_number"),
            is_signed=payload.get("is_signed"),
            signature_date=payload.get("signature_date"),
            entity_name=payload.get("entity_name"),
            business_name=payload.get("business_name"),
        )
        if not updated:
            return {"success": False, "executed": False, "result": None, "error": "Taxpayer not found or row version conflict"}
        return {"success": True, "executed": True, "result": updated.to_dict(), "error": None}
    except ValueError as e:
        logger.warning("Update taxpayer validation error: %s", e)
        return {"success": False, "executed": False, "result": None, "error": str(e)}
    except Exception as e:
        logger.exception("Execute update_taxpayer failed: %s", e)
        return {"success": False, "executed": False, "result": None, "error": str(e)}


def _execute_backfill_w9(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Backfill W9 from SharePoint: get folder from DriveItemVendor connector (when vendor has linked folder),
    or from payload (sharepoint_drive_id + sharepoint_folder_item_id or sharepoint_folder_path).
    Download first W9-like file, upload to blob, create Attachment and TaxpayerAttachment.
    Payload must include: taxpayer_public_id.
    Folder source (one of): (vendor_id or vendor_public_id) -> connector; or sharepoint_drive_id + sharepoint_folder_item_id; or sharepoint_drive_id + sharepoint_folder_path.
    """
    taxpayer_public_id = payload.get("taxpayer_public_id")
    if not taxpayer_public_id:
        return {
            "success": False,
            "executed": False,
            "result": None,
            "error": "Missing taxpayer_public_id in payload",
        }

    try:
        from entities.taxpayer.business.service import TaxpayerService
        from entities.attachment.business.service import AttachmentService
        from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService

        taxpayer_svc = TaxpayerService()
        taxpayer = taxpayer_svc.read_by_public_id(public_id=taxpayer_public_id)
        if not taxpayer or not taxpayer.id:
            return {"success": False, "executed": False, "result": None, "error": "Taxpayer not found"}

        drive_id: Optional[str] = None
        folder_id: Optional[str] = None

        # 1) Resolve folder from Vendor connector if payload has vendor
        vendor_id = payload.get("vendor_id")
        vendor_public_id = payload.get("vendor_public_id")
        if vendor_id is not None or vendor_public_id:
            try:
                from entities.vendor.business.service import VendorService
                from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import DriveItemVendorConnector
                vendor = VendorService().read_by_public_id(public_id=vendor_public_id) if vendor_public_id else VendorService().read_by_id(id=int(vendor_id))
                if vendor and vendor.id:
                    connector = DriveItemVendorConnector()
                    folder_info = connector.get_driveitem_for_vendor(vendor_id=int(vendor.id))
                    if folder_info and folder_info.get("drive_id") and folder_info.get("item_id"):
                        drive_id = folder_info["drive_id"]
                        folder_id = folder_info["item_id"]
            except Exception as e:
                logger.warning("Could not resolve folder from vendor connector: %s", e)

        # 2) Else use payload sharepoint_folder_item_id or sharepoint_folder_path
        if not drive_id or not folder_id:
            drive_id = payload.get("sharepoint_drive_id")
            folder_id = payload.get("sharepoint_folder_item_id")
            if not folder_id and drive_id and payload.get("sharepoint_folder_path"):
                from integrations.ms.sharepoint.external import client as sp_client
                folder_id = _resolve_sharepoint_folder_id(sp_client, drive_id, payload.get("sharepoint_folder_path", "").strip("/"))

        if not drive_id or not folder_id:
            return {
                "success": False,
                "executed": False,
                "result": None,
                "error": "Could not resolve SharePoint folder: link a folder to the vendor or provide sharepoint_drive_id and sharepoint_folder_path (or sharepoint_folder_item_id).",
            }

        from integrations.ms.sharepoint.external import client as sp_client

        # List children and pick first W9-like file
        list_result = sp_client.list_drive_item_children(drive_id=drive_id, item_id=folder_id)
        if list_result.get("status_code") != 200:
            return {"success": False, "executed": False, "result": None, "error": list_result.get("message", "Failed to list folder")}
        items = list_result.get("items") or []
        w9_item = _pick_w9_file(items)
        if not w9_item:
            return {"success": False, "executed": False, "result": None, "error": "No W9-like file found in SharePoint folder"}

        # Download file content
        content_result = sp_client.get_drive_item_content(drive_id=drive_id, item_id=w9_item["item_id"])
        if content_result.get("status_code") != 200 or content_result.get("content") is None:
            return {"success": False, "executed": False, "result": None, "error": content_result.get("message", "Failed to download file")}
        content = content_result["content"]
        content_type = content_result.get("content_type") or "application/octet-stream"
        filename = w9_item.get("name") or "w9.pdf"

        # Upload to Azure Blob
        blob_name = f"w9_backfill/{taxpayer.id}/{uuid.uuid4().hex[:12]}_{filename}"
        from shared.storage import AzureBlobStorage
        try:
            blob_client = AzureBlobStorage()
            blob_url = blob_client.upload_file(blob_name=blob_name, file_content=content, content_type=content_type)
        except Exception as e:
            logger.exception("Blob upload failed: %s", e)
            return {"success": False, "executed": False, "result": None, "error": f"Blob upload failed: {e}"}

        # Create Attachment
        att_svc = AttachmentService()
        file_hash = att_svc.calculate_hash(content)
        file_extension = (filename.rsplit(".", 1)[-1].lower() if "." in filename else None) or "pdf"
        attachment = att_svc.create(
            filename=filename,
            original_filename=filename,
            file_extension=file_extension,
            content_type=content_type,
            file_size=len(content),
            file_hash=file_hash,
            blob_url=blob_url,
            category="W9",
            tags="W9",
        )

        # Link to taxpayer
        ta_svc = TaxpayerAttachmentService()
        ta_svc.create(taxpayer_public_id=taxpayer_public_id, attachment_public_id=str(attachment.public_id))

        return {
            "success": True,
            "executed": True,
            "result": {"attachment_id": attachment.id, "attachment_public_id": str(attachment.public_id), "blob_url": blob_url},
            "error": None,
        }
    except Exception as e:
        logger.exception("Execute backfill_w9 failed: %s", e)
        return {"success": False, "executed": False, "result": None, "error": str(e)}


def _resolve_sharepoint_folder_id(sp_client: Any, drive_id: str, folder_path: str) -> Optional[str]:
    """
    Resolve a folder's item_id by path using existing client: list_drive_root_children
    and list_drive_item_children. Path is relative to drive root (e.g. "Vendors/ACME").
    """
    segments = [s for s in folder_path.split("/") if s]
    if not segments:
        return None
    # First segment: list root children
    root_result = sp_client.list_drive_root_children(drive_id=drive_id)
    if root_result.get("status_code") != 200:
        return None
    items = root_result.get("items") or []
    current = next((it for it in items if (it.get("name") or "").strip() == segments[0]), None)
    if not current or current.get("item_type") != "folder":
        return None
    current_id = current.get("item_id")
    # Remaining segments: list children of current folder
    for seg in segments[1:]:
        list_result = sp_client.list_drive_item_children(drive_id=drive_id, item_id=current_id)
        if list_result.get("status_code") != 200:
            return None
        items = list_result.get("items") or []
        current = next((it for it in items if (it.get("name") or "").strip() == seg), None)
        if not current or current.get("item_type") != "folder":
            return None
        current_id = current.get("item_id")
    return current_id


def _pick_w9_file(items: list) -> Optional[Dict[str, Any]]:
    """Return first drive item that looks like a W9: file with name containing w9/w-9, else first PDF."""
    for item in items:
        if item.get("item_type") != "file":
            continue
        name = (item.get("name") or "").lower()
        if any(s in name for s in W9_NAME_SUBSTRINGS):
            return item
    for item in items:
        if item.get("item_type") == "file":
            ext = (item.get("name") or "").rsplit(".", 1)[-1].lower() if "." in (item.get("name") or "") else ""
            if ext in W9_EXTENSIONS:
                return item
    return None
