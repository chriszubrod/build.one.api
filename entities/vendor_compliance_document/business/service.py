# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.attachment.business.service import AttachmentService
from entities.vendor.business.service import VendorService
from entities.vendor_compliance_document.business.folder_helpers import build_export_filename
from entities.vendor_compliance_document.business.model import VendorComplianceDocument
from entities.vendor_compliance_document.persistence.repo import VendorComplianceDocumentRepository
from shared.authz import current_user_id

logger = logging.getLogger(__name__)


class VendorComplianceDocumentService:
    """
    Service for VendorComplianceDocument entity business operations.
    """

    def __init__(self, repo: Optional[VendorComplianceDocumentRepository] = None):
        """Initialize the VendorComplianceDocumentService."""
        self.repo = repo or VendorComplianceDocumentRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        vendor_public_id: str,
        document_type: str,
        issuing_authority: Optional[str] = None,
        document_number: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        attachment_public_id: Optional[str] = None,
        verification_status: str = "Received",
        push_to_folder: bool = True,
    ) -> VendorComplianceDocument:
        """
        Create a new vendor compliance document.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        attachment_id = None
        if attachment_public_id:
            attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
            if not attachment or not attachment.id:
                raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
            attachment_id = int(attachment.id)

        doc = self.repo.create(
            vendor_id=int(vendor.id),
            document_type=document_type,
            issuing_authority=issuing_authority,
            document_number=document_number,
            classification=classification,
            issue_date=issue_date,
            expiry_date=expiry_date,
            attachment_id=attachment_id,
            verification_status=verification_status,
            created_by_user_id=current_user_id.get(),
        )

        if push_to_folder:
            try:
                self._enqueue_folder_export(int(vendor.id), attachment_id)
            except Exception:
                logger.exception(
                    "Failed to enqueue vendor compliance folder export after create "
                    "(vendor_id=%s, attachment_id=%s)",
                    vendor.id,
                    attachment_id,
                )

        return doc

    def read_by_id(self, id: str) -> Optional[VendorComplianceDocument]:
        """
        Read a vendor compliance document by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[VendorComplianceDocument]:
        """
        Read a vendor compliance document by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[VendorComplianceDocument]:
        """
        Read vendor compliance documents by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id=vendor_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = 1,
        row_version: str,
        issuing_authority: Optional[str] = None,
        document_number: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        attachment_public_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        push_to_folder: bool = True,
    ) -> Optional[VendorComplianceDocument]:
        """
        Update a vendor compliance document by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        attachment_changed = attachment_public_id is not None

        existing.row_version = row_version
        if issuing_authority is not None:
            existing.issuing_authority = issuing_authority
        if document_number is not None:
            existing.document_number = document_number
        if classification is not None:
            existing.classification = classification
        if issue_date is not None:
            existing.issue_date = issue_date
        if expiry_date is not None:
            existing.expiry_date = expiry_date
        if verification_status is not None:
            existing.verification_status = verification_status
        if attachment_public_id is not None:
            attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
            if not attachment or not attachment.id:
                raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
            existing.attachment_id = int(attachment.id)

        updated = self.repo.update_by_id(existing)

        if push_to_folder and updated and updated.attachment_id and attachment_changed:
            try:
                self._enqueue_folder_export(int(updated.vendor_id), int(updated.attachment_id))
            except Exception:
                logger.exception(
                    "Failed to enqueue vendor compliance folder export after update "
                    "(vendor_id=%s, attachment_id=%s)",
                    updated.vendor_id,
                    updated.attachment_id,
                )

        return updated

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = 1) -> Optional[VendorComplianceDocument]:
        """
        Delete a vendor compliance document by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            if self.repo.delete_by_id(int(existing.id)):
                return existing
        return None

    def _enqueue_folder_export(self, vendor_id: int, attachment_id: Optional[int]) -> None:
        """Queue SharePoint and/or Box upload of a compliance-doc attachment to the vendor folder."""
        if attachment_id is None:
            return
        attachment = AttachmentService().read_by_id(id=attachment_id)
        if not attachment or not attachment.blob_url:
            return

        # SharePoint export (unchanged logic, now its own guarded block)
        try:
            from integrations.ms.outbox.business.service import MsOutboxService
            from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
                DriveItemVendorConnector,
            )

            folder = DriveItemVendorConnector().get_driveitem_for_vendor(vendor_id)
            if folder and folder.get("drive_id") and folder.get("item_id"):
                filename = build_export_filename(
                    attachment.original_filename or attachment.filename or "document",
                    str(attachment.public_id),
                )
                content_type = attachment.content_type or "application/octet-stream"
                queued = MsOutboxService().enqueue_sharepoint_upload(
                    entity_type="Attachment",
                    entity_public_id=str(attachment.public_id),
                    drive_id=folder["drive_id"],
                    parent_item_id=folder["item_id"],
                    filename=filename,
                    content_type=content_type,
                    blob_path=attachment.blob_url,
                    attachment_id=attachment_id,
                )
                if queued is None:
                    logger.info(
                        "SharePoint export enqueue skipped (writes disabled or failure) "
                        "for attachment_id=%s vendor_id=%s",
                        attachment_id,
                        vendor_id,
                    )
        except Exception:
            logger.exception(
                "SharePoint compliance export enqueue failed for attachment_id=%s vendor_id=%s",
                attachment_id,
                vendor_id,
            )

        # Box export (parallel, failure-isolated)
        try:
            from integrations.box.folder.persistence.repo import BoxVendorFolderRepository
            from integrations.box.outbox.business.service import BoxOutboxService

            box_mapping = BoxVendorFolderRepository().read_by_vendor_id(vendor_id)
            if box_mapping and box_mapping.get("box_folder_id"):
                BoxOutboxService().enqueue_box_upload(
                    entity_type="vendor_compliance_document",
                    entity_public_id=str(attachment.public_id),
                    doc_kind="attachment",
                    blob_path=attachment.blob_url,
                    filename=attachment.original_filename or attachment.filename or "document",
                    content_type=attachment.content_type or "application/octet-stream",
                    box_folder_id=box_mapping["box_folder_id"],
                    attachment_id=attachment_id,
                    project_id=None,
                )
        except Exception:
            logger.exception(
                "Box compliance export enqueue failed for attachment_id=%s vendor_id=%s",
                attachment_id,
                vendor_id,
            )
