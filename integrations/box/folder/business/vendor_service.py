"""
Box mirror of SharePoint ``VendorFolderService`` for vendor compliance folders.

The import path's blob-upload + attachment + compliance-doc create back-half
duplicates ``entities/vendor_compliance_document/business/folder_service.py``
``import_file`` and is a Pass-2 DRY candidate.
"""

# Python Standard Library Imports
import logging
import mimetypes
from typing import Optional
from uuid import uuid4

# Third-party Imports

# Local Imports
from entities.attachment.business.service import AttachmentService
from entities.vendor.business.service import VendorService
from entities.vendor_compliance_document.business.folder_helpers import (
    is_bl_hint,
    is_compliance_hint,
    is_w9_hint,
    select_duplicate_compliance_doc,
    walk_folder_tree,
)
from entities.vendor_compliance_document.business.service import VendorComplianceDocumentService
from integrations.box.base.client import BoxHttpClient
from integrations.box.folder.persistence.repo import (
    BoxFolderRepository,
    BoxVendorFolderRepository,
)
from shared.authz import current_user_id
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)

WALK_MAX_DEPTH = 25
WALK_MAX_ITEMS = 5000

IMPORTABLE_DOCUMENT_TYPES = frozenset({
    "CONTRACTORS_LICENSE",
    "CERTIFICATE_OF_INSURANCE",
})


def _box_list_children(client: BoxHttpClient, folder_id: str) -> dict:
    """Paginated Box folder listing adapted for ``walk_folder_tree``."""
    entries: list[dict] = []
    offset = 0
    page_size = 1000
    while True:
        result = client.get(
            f"folders/{folder_id}/items",
            params={
                "fields": "id,name,type,size",
                "limit": page_size,
                "offset": offset,
            },
            operation_name="box.vendor_folder.list_children",
        )
        page_entries = result.get("entries") or []
        entries.extend(page_entries)
        total = result.get("total_count", 0)
        offset += len(page_entries)
        if not page_entries or offset >= total:
            break

    items = [
        {
            "item_type": "folder" if entry.get("type") == "folder" else "file",
            "item_id": str(entry["id"]),
            "name": entry.get("name"),
            "size": entry.get("size"),
        }
        for entry in entries
    ]
    return {"items": items}


class BoxVendorFolderService:
    """Orchestrates Box folder link/import for vendor compliance docs."""

    def __init__(
        self,
        folder_repo: Optional[BoxFolderRepository] = None,
        vendor_folder_repo: Optional[BoxVendorFolderRepository] = None,
    ):
        self.folder_repo = folder_repo or BoxFolderRepository()
        self.vendor_folder_repo = vendor_folder_repo or BoxVendorFolderRepository()

    def browse(self, box_folder_id: Optional[str] = None) -> list[dict]:
        """List folders/files at Box root (``0``) or within a folder."""
        fid = box_folder_id or "0"
        with BoxHttpClient() as client:
            return _box_list_children(client, fid)["items"]

    def get_linked_folder(self, vendor_public_id: str) -> Optional[dict]:
        """Return the vendor's linked Box folder metadata, or None."""
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")
        return self.vendor_folder_repo.read_by_vendor_id(int(vendor.id))

    def link_folder(self, vendor_public_id: str, box_folder_id: str) -> dict:
        """Link a Box folder to a vendor (visibility-proof GET before persist)."""
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        box_folder_id = str(box_folder_id).strip()

        with BoxHttpClient() as client:
            folder_data = client.get(
                f"folders/{box_folder_id}",
                params={"fields": "id,name,parent"},
                operation_name="box.vendor_folder.get",
            )

        name = folder_data.get("name") or f"folder-{box_folder_id}"
        existing = self.vendor_folder_repo.read_by_vendor_id(int(vendor.id))
        if existing:
            if existing.get("box_folder_id") == box_folder_id:
                return existing
            raise ValueError(
                "Vendor already linked to a different Box folder; unlink first"
            )

        parent = folder_data.get("parent") or {}
        parent_id = str(parent["id"]) if parent.get("id") else None

        folder_row = self.folder_repo.read_by_box_folder_id(box_folder_id)
        if not folder_row:
            folder_row = self.folder_repo.create(
                box_folder_id=box_folder_id,
                name=name,
                parent_box_folder_id=parent_id,
            )

        if self.vendor_folder_repo.read_by_box_folder_id(int(folder_row.id)):
            raise ValueError("That Box folder is already linked to another vendor")

        self.vendor_folder_repo.create(
            vendor_id=int(vendor.id),
            box_folder_id=int(folder_row.id),
            created_by_user_id=current_user_id.get(),
        )
        return self.vendor_folder_repo.read_by_vendor_id(int(vendor.id))

    def unlink_folder(self, vendor_public_id: str) -> dict:
        """Remove the Box folder link for a vendor."""
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        existing = self.vendor_folder_repo.read_by_vendor_id(int(vendor.id))
        if not existing:
            raise ValueError("No Box folder linked for this vendor")

        self.vendor_folder_repo.delete_by_vendor_id(int(vendor.id))
        return {"vendor_public_id": vendor_public_id, "unlinked": True}

    def list_files(self, vendor_public_id: str) -> list[dict]:
        """Walk the vendor's linked Box folder tree and return all files."""
        mapping = self.get_linked_folder(vendor_public_id)
        if not mapping:
            raise ValueError("No Box folder linked for this vendor")

        box_folder_id = mapping["box_folder_id"]
        with BoxHttpClient() as client:
            files = walk_folder_tree(
                lambda _drive_id, item_id: _box_list_children(client, item_id),
                "",
                box_folder_id,
                max_depth=WALK_MAX_DEPTH,
                max_items=WALK_MAX_ITEMS,
            )

        return [
            {
                "item_id": file.get("item_id"),
                "name": file.get("name"),
                "folder_path": file.get("folder_path", ""),
                "size": file.get("size"),
                "compliance_hint": is_compliance_hint(file.get("name") or ""),
                "w9_hint": is_w9_hint(file.get("name") or ""),
                "bl_hint": is_bl_hint(file.get("name") or ""),
            }
            for file in files
        ]

    def import_file(
        self,
        *,
        vendor_public_id: str,
        box_file_id: str,
        document_type: str,
        issuing_authority: Optional[str] = None,
        document_number: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
    ) -> dict:
        """Import a file from the vendor's linked Box folder into compliance docs."""
        if document_type not in IMPORTABLE_DOCUMENT_TYPES:
            raise ValueError(
                f"document_type '{document_type}' is not importable via Box folder"
            )

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        mapping = self.get_linked_folder(vendor_public_id)
        if not mapping:
            raise ValueError("No Box folder linked for this vendor")

        box_folder_id = mapping["box_folder_id"]
        box_file_id = str(box_file_id).strip()

        with BoxHttpClient() as client:
            files = walk_folder_tree(
                lambda _drive_id, item_id: _box_list_children(client, item_id),
                "",
                box_folder_id,
                max_depth=WALK_MAX_DEPTH,
                max_items=WALK_MAX_ITEMS,
            )
            matched = next(
                (f for f in files if f.get("item_id") == box_file_id),
                None,
            )
            if not matched:
                if len(files) == WALK_MAX_ITEMS:
                    raise ValueError(
                        "Folder has too many files to enumerate for import verification; "
                        "please reduce folder size or contact support"
                    )
                raise ValueError("File is not in this vendor Box folder")

            filename = matched.get("name") or "document"
            file_bytes = client.download_file(box_file_id)

        attachment_service = AttachmentService()
        file_hash = attachment_service.calculate_hash(file_bytes)

        existing_docs = VendorComplianceDocumentService().read_by_vendor_id(int(vendor.id))
        attachment_ids = [int(d.attachment_id) for d in existing_docs if d.attachment_id]
        hash_by_id: dict[int, str | None] = {}
        if attachment_ids:
            att_list = AttachmentService().read_by_ids(attachment_ids)
            hash_by_id = {int(a.id): a.file_hash for a in att_list}
        candidates = [
            (
                d.document_type,
                hash_by_id.get(int(d.attachment_id)) if d.attachment_id else None,
                d,
            )
            for d in existing_docs
        ]
        dup = select_duplicate_compliance_doc(candidates, document_type, file_hash)
        if dup is not None:
            return {**dup.to_dict(), "already_on_file": True}

        ext = AttachmentService.extract_extension(filename) or ""
        dotted_ext = f".{ext}" if ext else ""
        guessed_type, _ = mimetypes.guess_type(filename)
        content_type = guessed_type or "application/pdf"

        blob_name = f"vendor_compliance/{uuid4().hex}{dotted_ext}"
        blob_url = AzureBlobStorage().upload_file(
            blob_name=blob_name,
            file_content=file_bytes,
            content_type=content_type,
        )
        attachment = attachment_service.create(
            filename=blob_name,
            original_filename=filename,
            file_extension=ext,
            content_type=content_type,
            file_size=len(file_bytes),
            file_hash=file_hash,
            blob_url=blob_url,
            category="vendor_compliance",
        )

        doc = VendorComplianceDocumentService().create(
            vendor_public_id=vendor_public_id,
            document_type=document_type,
            issuing_authority=issuing_authority,
            document_number=document_number,
            classification=classification,
            issue_date=issue_date,
            expiry_date=expiry_date,
            attachment_public_id=str(attachment.public_id),
            verification_status="Received",
            push_to_folder=False,
        )
        return doc.to_dict()
