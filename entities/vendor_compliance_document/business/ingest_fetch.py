"""Shared PDF fetch + attachment prove/back-half for vendor compliance document ingest."""

import mimetypes
from typing import Optional, Tuple
from uuid import uuid4

WALK_MAX_DEPTH = 25
WALK_MAX_ITEMS = 5000


def load_compliance_pdf_and_attachment(
    vendor,
    *,
    attachment_public_id: Optional[str] = None,
    provider: Optional[str] = None,
    file_id: Optional[str] = None,
    blob_category: str,
) -> Tuple[bytes, str, str, str]:
    if attachment_public_id:
        return _load_from_attachment(attachment_public_id)

    if provider in ("sharepoint", "box") and file_id:
        if provider == "sharepoint":
            return _load_from_sharepoint(vendor, file_id, blob_category)
        return _load_from_box(vendor, file_id, blob_category)

    raise ValueError(
        "Provide attachment_public_id or provider ('sharepoint'|'box') with file_id"
    )


def _load_from_attachment(attachment_public_id: str) -> Tuple[bytes, str, str, str]:
    from entities.attachment.business.service import AttachmentService
    from shared.storage import AzureBlobStorage

    att = AttachmentService().read_by_public_id(public_id=attachment_public_id)
    if not att:
        raise ValueError("attachment not found")
    data, _ = AzureBlobStorage().download_file(att.blob_url)
    name = att.original_filename or att.filename or "document.pdf"
    content_type = att.content_type or "application/pdf"
    return data, str(att.public_id), name, content_type


def _ensure_attachment_from_bytes(
    data: bytes, name: str, blob_category: str
) -> Tuple[str, str]:
    from entities.attachment.business.service import AttachmentService
    from shared.storage import AzureBlobStorage

    attachment_service = AttachmentService()
    file_hash = attachment_service.calculate_hash(data)
    existing = attachment_service.read_by_hash(file_hash)
    if existing:
        content_type = existing.content_type or "application/pdf"
        return str(existing.public_id), content_type

    ext = AttachmentService.extract_extension(name) or ""
    dotted = f".{ext}" if ext else ""
    guessed_type, _ = mimetypes.guess_type(name)
    content_type = guessed_type or "application/pdf"
    blob_name = f"{blob_category}/{uuid4().hex}{dotted}"
    blob_url = AzureBlobStorage().upload_file(
        blob_name=blob_name,
        file_content=data,
        content_type=content_type,
    )
    att = attachment_service.create(
        filename=blob_name,
        original_filename=name,
        file_extension=ext,
        content_type=content_type,
        file_size=len(data),
        file_hash=file_hash,
        blob_url=blob_url,
        category=blob_category,
    )
    return str(att.public_id), content_type


def _load_from_sharepoint(
    vendor, file_id: str, blob_category: str
) -> Tuple[bytes, str, str, str]:
    from entities.vendor_compliance_document.business.folder_helpers import walk_folder_tree
    from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
        DriveItemVendorConnector,
    )
    from integrations.ms.sharepoint.external import client as sp_client

    folder = DriveItemVendorConnector().get_driveitem_for_vendor(int(vendor.id))
    if not folder or not folder.get("drive_id") or not folder.get("item_id"):
        raise ValueError("No SharePoint folder linked for this vendor")

    drive_id = folder["drive_id"]
    graph_item_id = str(file_id).strip()
    files = walk_folder_tree(
        sp_client.list_drive_item_children,
        drive_id,
        folder["item_id"],
        max_depth=WALK_MAX_DEPTH,
        max_items=WALK_MAX_ITEMS,
    )
    matched = next((f for f in files if f.get("item_id") == graph_item_id), None)
    if not matched:
        if len(files) == WALK_MAX_ITEMS:
            raise ValueError(
                "Folder has too many files to enumerate for import verification; "
                "please reduce folder size or contact support"
            )
        raise ValueError("File is not in this vendor folder")

    name = matched.get("name") or "document.pdf"
    content_result = sp_client.get_drive_item_content(drive_id, graph_item_id)
    if content_result.get("status_code", 500) >= 400 or content_result.get("content") is None:
        raise ValueError(content_result.get("message", "Failed to download file from SharePoint"))
    data = content_result["content"]
    att_public_id, content_type = _ensure_attachment_from_bytes(data, name, blob_category)
    return data, att_public_id, name, content_type


def _load_from_box(vendor, file_id: str, blob_category: str) -> Tuple[bytes, str, str, str]:
    from entities.vendor_compliance_document.business.folder_helpers import walk_folder_tree
    from integrations.box.base.client import BoxHttpClient
    from integrations.box.folder.business.vendor_service import _box_list_children
    from integrations.box.folder.persistence.repo import BoxVendorFolderRepository

    mapping = BoxVendorFolderRepository().read_by_vendor_id(int(vendor.id))
    if not mapping or not mapping.get("box_folder_id"):
        raise ValueError("No Box folder linked for this vendor")

    box_folder_id = mapping["box_folder_id"]
    box_file_id = str(file_id).strip()

    with BoxHttpClient() as client:
        files = walk_folder_tree(
            lambda _drive_id, item_id: _box_list_children(client, item_id),
            None,
            box_folder_id,
            max_depth=WALK_MAX_DEPTH,
            max_items=WALK_MAX_ITEMS,
        )
        matched = next((f for f in files if f.get("item_id") == box_file_id), None)
        if not matched:
            if len(files) == WALK_MAX_ITEMS:
                raise ValueError(
                    "Folder has too many files to enumerate for import verification; "
                    "please reduce folder size or contact support"
                )
            raise ValueError("File is not in this vendor Box folder")

        name = matched.get("name") or "document.pdf"
        data = client.download_file(box_file_id)

    att_public_id, content_type = _ensure_attachment_from_bytes(data, name, blob_category)
    return data, att_public_id, name, content_type
