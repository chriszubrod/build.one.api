# Python Standard Library Imports
import logging
import mimetypes
from typing import Optional
from uuid import uuid4

# Third-party Imports

# Local Imports
from entities.attachment.business.service import AttachmentService
from entities.vendor.business.service import VendorService
from entities.vendor_compliance.business.folder_helpers import (
    is_bl_hint,
    is_cl_hint,
    is_coi_hint,
    is_compliance_hint,
    is_w9_hint,
    select_duplicate_compliance_doc,
    walk_folder_tree,
)
from entities.certificate_of_insurance.business.service import CertificateOfInsuranceService
from integrations.ms.sharepoint.drive.business.service import MsDriveService
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)

WALK_MAX_DEPTH = 25
WALK_MAX_ITEMS = 5000

IMPORTABLE_DOCUMENT_TYPES = frozenset({"CERTIFICATE_OF_INSURANCE"})


class VendorFolderService:
    """Orchestrates SharePoint folder link/import/export for vendor compliance docs."""

    def list_drives(self) -> list[dict]:
        """Return linked SharePoint drives (same source as the MS drive list route).

        Normalized to `drive_public_id` (not the raw model's `public_id`) so the
        field matches the browse endpoint's query param and the web contract.
        """
        drives = MsDriveService().read_all()
        return [
            {
                "drive_public_id": drive.public_id,
                "name": drive.name,
                "web_url": drive.web_url,
                "drive_type": drive.drive_type,
            }
            for drive in drives
        ]

    def browse(self, drive_public_id: str, item_id: Optional[str] = None) -> list[dict]:
        """List folders/files at drive root or within a folder."""
        from integrations.ms.sharepoint.external import client as sp_client

        drive = MsDriveService().read_by_public_id(public_id=drive_public_id)
        if not drive or not drive.drive_id:
            raise ValueError(f"Drive with public_id '{drive_public_id}' not found")

        if item_id is None:
            result = sp_client.list_drive_root_children(drive.drive_id)
        else:
            result = sp_client.list_drive_item_children(drive.drive_id, item_id)

        if result.get("status_code", 500) >= 400:
            raise ValueError(result.get("message", "Failed to browse SharePoint folder"))

        return result.get("items", [])

    def get_linked_folder(self, vendor_public_id: str) -> Optional[dict]:
        """Return the vendor's linked SharePoint folder metadata, or None."""
        from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
            DriveItemVendorConnector,
        )

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        return DriveItemVendorConnector().get_driveitem_for_vendor(int(vendor.id))

    def link_folder(self, vendor_public_id: str, drive_public_id: str, graph_item_id: str) -> dict:
        """Link a SharePoint folder to a vendor."""
        from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
            DriveItemVendorConnector,
        )

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        result = DriveItemVendorConnector().link_driveitem_to_vendor(
            vendor_id=int(vendor.id),
            drive_public_id=drive_public_id,
            graph_item_id=graph_item_id,
        )
        status_code = result.get("status_code", 500)
        if status_code not in (200, 201):
            raise ValueError(result.get("message", "Failed to link folder"))

        return {
            "mapping": result.get("mapping"),
            "driveitem": result.get("driveitem"),
        }

    def unlink_folder(self, vendor_public_id: str) -> dict:
        """Remove the SharePoint folder link for a vendor."""
        from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
            DriveItemVendorConnector,
        )

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        result = DriveItemVendorConnector().unlink_by_vendor_id(int(vendor.id))
        status_code = result.get("status_code", 500)
        if status_code == 404:
            raise ValueError(result.get("message", "No linked folder found for this vendor"))
        if status_code >= 400:
            raise ValueError(result.get("message", "Failed to unlink folder"))

        return {"mapping": result.get("mapping")}

    def list_files(self, vendor_public_id: str) -> list[dict]:
        """Walk the vendor's linked folder tree and return all files."""
        from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
            DriveItemVendorConnector,
        )
        from integrations.ms.sharepoint.external import client as sp_client

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        folder = DriveItemVendorConnector().get_driveitem_for_vendor(int(vendor.id))
        if not folder or not folder.get("drive_id") or not folder.get("item_id"):
            raise ValueError("No SharePoint folder linked for this vendor")

        files = walk_folder_tree(
            sp_client.list_drive_item_children,
            folder["drive_id"],
            folder["item_id"],
            max_depth=WALK_MAX_DEPTH,
            max_items=WALK_MAX_ITEMS,
        )

        return [
            {
                "graph_item_id": file["item_id"],
                "name": file.get("name"),
                "folder_path": file.get("folder_path", ""),
                "size": file.get("size"),
                "compliance_hint": is_compliance_hint(file.get("name") or ""),
                "w9_hint": is_w9_hint(file.get("name") or ""),
                "bl_hint": is_bl_hint(file.get("name") or ""),
                "cl_hint": is_cl_hint(file.get("name") or ""),
                "coi_hint": is_coi_hint(file.get("name") or ""),
            }
            for file in files
        ]

    def import_file(
        self,
        *,
        vendor_public_id: str,
        graph_item_id: str,
        document_type: str,
        issuing_authority: Optional[str] = None,
        document_number: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
    ) -> dict:
        """Import a file from the vendor's linked SharePoint folder into compliance docs."""
        from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
            DriveItemVendorConnector,
        )
        from integrations.ms.sharepoint.external import client as sp_client

        if document_type not in IMPORTABLE_DOCUMENT_TYPES:
            raise ValueError(
                f"document_type '{document_type}' is not importable via SharePoint folder"
            )

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        folder = DriveItemVendorConnector().get_driveitem_for_vendor(int(vendor.id))
        if not folder or not folder.get("drive_id") or not folder.get("item_id"):
            raise ValueError("No SharePoint folder linked for this vendor")

        drive_id = folder["drive_id"]
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
            raise ValueError("File is not in this vendor's folder")

        sp_filename = matched.get("name") or "document"

        content_result = sp_client.get_drive_item_content(drive_id, graph_item_id)
        if content_result.get("status_code", 500) >= 400 or content_result.get("content") is None:
            raise ValueError(content_result.get("message", "Failed to download file from SharePoint"))

        file_bytes = content_result["content"]
        ext = AttachmentService.extract_extension(sp_filename) or ""
        dotted_ext = f".{ext}" if ext else ""
        guessed_type, _ = mimetypes.guess_type(sp_filename)
        content_type = guessed_type or "application/pdf"

        attachment_service = AttachmentService()
        file_hash = attachment_service.calculate_hash(file_bytes)

        dup = self._find_existing_duplicate(int(vendor.id), document_type, file_hash)
        if dup is not None:
            return {**dup.to_dict(), "already_on_file": True}

        blob_name = f"vendor_compliance/{uuid4().hex}{dotted_ext}"
        blob_url = AzureBlobStorage().upload_file(
            blob_name=blob_name,
            file_content=file_bytes,
            content_type=content_type,
        )
        attachment = attachment_service.create(
            filename=blob_name,
            original_filename=sp_filename,
            file_extension=ext,
            content_type=content_type,
            file_size=len(file_bytes),
            file_hash=file_hash,
            blob_url=blob_url,
            category="vendor_compliance",
        )

        cert = CertificateOfInsuranceService().create(
            vendor_public_id=vendor_public_id,
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            attachment_id=int(attachment.id),
            verification_status="Received",
        )
        return cert.to_dict()

    def _find_existing_duplicate(
        self, vendor_id: int, document_type: str, file_hash: str
    ):
        """Return an existing cert for this vendor whose attachment file_hash matches, else None."""
        certs = CertificateOfInsuranceService().read_by_vendor_id(vendor_id)
        attachment_ids = [int(c.attachment_id) for c in certs if c.attachment_id]
        hash_by_id: dict[int, str | None] = {}
        if attachment_ids:
            att_list = AttachmentService().read_by_ids(attachment_ids)
            hash_by_id = {int(a.id): a.file_hash for a in att_list}
        candidates = [
            (
                "CERTIFICATE_OF_INSURANCE",
                hash_by_id.get(int(c.attachment_id)) if c.attachment_id else None,
                c,
            )
            for c in certs
        ]
        return select_duplicate_compliance_doc(candidates, "CERTIFICATE_OF_INSURANCE", file_hash)
