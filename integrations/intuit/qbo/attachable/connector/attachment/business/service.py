# Python Standard Library Imports
import logging
import os
import uuid
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.attachable.connector.attachment.business.model import AttachableAttachment
from integrations.intuit.qbo.attachable.connector.attachment.persistence.repo import AttachableAttachmentRepository
from integrations.intuit.qbo.attachable.business.model import QboAttachable
from integrations.intuit.qbo.attachable.persistence.repo import QboAttachableRepository
from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.base.identity_fastpath import (
    resolve_mapping_state,
    run_identity_fastpath,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.attachment.business.service import AttachmentService
from entities.attachment.business.model import Attachment
from shared.storage import AzureBlobStorage, AzureBlobStorageError
from shared.pdf_utils import ensure_pdf, compact_pdf

logger = logging.getLogger(__name__)


class AttachableAttachmentConnector:
    """
    Connector service for synchronization between QboAttachable and Attachment modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[AttachableAttachmentRepository] = None,
        attachment_service: Optional[AttachmentService] = None,
        auth_service: Optional[QboAuthService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the AttachableAttachmentConnector."""
        self.mapping_repo = mapping_repo or AttachableAttachmentRepository()
        self.attachment_service = attachment_service or AttachmentService()
        self.auth_service = auth_service or QboAuthService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_attachable(
        self,
        qbo_attachable: QboAttachable,
        realm_id: str,
    ) -> Attachment:
        """
        Sync data from QboAttachable to Attachment module.

        This method:
        1. Resolves identity — dbo-native QboId/RealmId fast path first
           (U-279), falling back to the qbo.AttachableAttachment mapping
           table
        2. Downloads the file from QBO if needed
        3. Uploads to Azure Blob Storage
        4. Creates or updates the Attachment accordingly

        Args:
            qbo_attachable: QboAttachable record from local database
            realm_id: QBO realm ID for API access

        Returns:
            Attachment: The synced Attachment record
        """
        attachment = None

        # U-279 fast path: resolve identity directly against dbo.Attachment's
        # native QboId/RealmId (U-238c) before falling back to the
        # qbo.AttachableAttachment mapping-table hop below. Mirrors the
        # Customer/Project/Company/Address/VendorCredit Phase-4 pilot pattern
        # (U-276/277/278) — see docs/staging_removal_phase4_5_scoping.md §6/§10.
        #
        # The mapping-table state is checked BEFORE any write, not after —
        # the same check-before-write discipline U-276's round-3 review
        # established: writing to the dbo-identity-matched Attachment first
        # and detecting a conflict afterward could corrupt state in the case
        # the mapping table, not dbo identity, is actually still correct.
        # No `apply_fields`: unlike its five siblings, this connector uses the fast path
        # for identity RESOLUTION only — the field work (blob verification, re-download,
        # re-upload) happens in the shared block below, reached whether identity came
        # from the fast path or the legacy mapping table. So the mapping row is created
        # directly here (not via _create_mapping, which would also re-call
        # set_qbo_identity — redundant, since identity is already correct on `direct`).
        outcome = run_identity_fastpath(
            qbo_id=qbo_attachable.qbo_id,
            realm_id=realm_id,
            external_id=qbo_attachable.id,
            entity_label="Attachment",
            external_label="QboAttachable",
            mapping_label="AttachableAttachment",
            read_direct_by_qbo_identity=self.attachment_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_attachment_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_attachable_id,
            external_id_attr="qbo_attachable_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_identity_mapping_conflict_issue(
                    qbo_attachable=qbo_attachable,
                    dbo_attachment_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                    realm_id=realm_id,
                )
            ),
            conflict_message=lambda entity: (
                f"Attachment identity conflict for QboAttachable "
                f"{qbo_attachable.qbo_id} (id={qbo_attachable.id}): dbo.Attachment "
                f"{entity.id} already carries this identity but the mapping table "
                f"disagrees. Not auto-repointed; see the recorded reconciliation "
                f"issue. Skipping until a human resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                attachment_id=local_id, qbo_attachable_id=qbo_attachable.id
            ),
        )
        if outcome.hit:
            attachment = outcome.entity

        if attachment is None:
            # Legacy path: resolve via the qbo.AttachableAttachment mapping table.
            mapping = self.mapping_repo.read_by_qbo_attachable_id(qbo_attachable.id)
            if mapping:
                attachment = self.attachment_service.read_by_id(mapping.attachment_id)
                if not attachment:
                    # Mapping exists but Attachment not found - recreate
                    logger.warning(f"Mapping exists but Attachment {mapping.attachment_id} not found. Creating new.")
                    self.mapping_repo.delete_by_id(mapping.id)

        if attachment:
            # Verify the blob still exists in Azure storage with a lightweight
            # HEAD probe (exists()) rather than downloading the whole file.
            blob_ok = True
            if attachment.blob_url:
                try:
                    blob_ok = AzureBlobStorage().exists(attachment.blob_url)
                except Exception:
                    # Couldn't determine existence (auth/network) — treat as
                    # missing so we re-download (heals) rather than trusting it.
                    blob_ok = False
                if not blob_ok:
                    logger.warning(
                        f"Attachment {attachment.id} blob missing at {attachment.blob_url} — "
                        f"re-downloading from QBO for QboAttachable {qbo_attachable.id}"
                    )
            else:
                blob_ok = False

            if blob_ok:
                logger.info(f"Found existing Attachment {attachment.id} for QboAttachable {qbo_attachable.id}")
                existing_qbo_id = getattr(attachment, "qbo_id", None)
                existing_realm_id = getattr(attachment, "realm_id", None)
                if not (
                    (existing_qbo_id or "") == (qbo_attachable.qbo_id or "")
                    and (existing_realm_id or "") == (realm_id or "")
                ):
                    self.attachment_service.repo.set_qbo_identity(
                        id=coerce_id(attachment.id),
                        qbo_id=qbo_attachable.qbo_id,
                        realm_id=realm_id,
                    )
                return attachment

            # Blob is missing — re-download from QBO and re-upload
            file_content = self._download_from_qbo(qbo_attachable, realm_id)
            if file_content:
                content_type = qbo_attachable.content_type or "application/octet-stream"
                file_name = qbo_attachable.file_name or f"attachment_{qbo_attachable.qbo_id}"
                file_content, content_type, file_extension = ensure_pdf(file_content, content_type, file_name)
                if file_extension == ".pdf":
                    file_name = self._ensure_pdf_filename(file_name)
                    file_content = compact_pdf(file_content)
                blob_url = self._upload_to_blob(
                    file_content=file_content,
                    file_name=file_name,
                    content_type=content_type,
                )
                # Update the existing Attachment record with the new blob URL
                self.attachment_service.update_by_public_id(
                    public_id=attachment.public_id,
                    row_version=attachment.row_version,
                    blob_url=blob_url,
                    file_size=len(file_content),
                    content_type=content_type,
                    file_extension=file_extension,
                )
                logger.info(f"Re-uploaded blob for Attachment {attachment.id} → {blob_url}")
                # Fresh bytes landed — queue text extraction (U-187). Deferred
                # to the sweep; DI never runs inline in the realm pull.
                self._mark_pending_extraction(attachment.id)
                # Re-read to get updated record
                refreshed = self.attachment_service.read_by_id(attachment.id)
                if refreshed:
                    self.attachment_service.repo.set_qbo_identity(
                        id=coerce_id(refreshed.id),
                        qbo_id=qbo_attachable.qbo_id,
                        realm_id=realm_id,
                    )
                return refreshed
            else:
                # Blob is gone AND re-download failed — do NOT return a record that
                # points at a missing blob (it would get linked to line items and
                # fail later in packets/exports). Raise so this attachable is skipped
                # this run and re-attempted on the next sync (attachments are
                # best-effort: the parent entity still projects).
                logger.error(f"Could not re-download file from QBO for Attachment {attachment.id}")
                raise RuntimeError(
                    f"Attachment {attachment.id} blob missing and re-download from QBO "
                    f"failed for QboAttachable {qbo_attachable.id}"
                )

        # Download file from QBO
        file_content = self._download_from_qbo(qbo_attachable, realm_id)
        
        if not file_content:
            raise ValueError(f"Failed to download file for QboAttachable {qbo_attachable.id}")
        
        # Ensure PDF: convert images to PDF
        content_type = qbo_attachable.content_type or "application/octet-stream"
        file_name = qbo_attachable.file_name or f"attachment_{qbo_attachable.qbo_id}"
        file_content, content_type, file_extension = ensure_pdf(file_content, content_type, file_name)
        if file_extension == ".pdf":
            file_name = self._ensure_pdf_filename(file_name)
            file_content = compact_pdf(file_content)

        # Calculate file hash
        file_hash = self.attachment_service.calculate_hash(file_content)
        
        # Check for duplicate by hash
        existing_by_hash = self.attachment_service.read_by_hash(file_hash)
        if existing_by_hash:
            logger.info(f"Found existing Attachment by hash for QboAttachable {qbo_attachable.id}")
            # Verify the hash-deduped attachment's blob is actually present. If it's
            # gone (or it never had a blob_url), heal it by re-uploading the content we
            # just downloaded — otherwise the healthy-subset contract would report a
            # missing-blob record as healthy and we'd link a broken attachment.
            blob_ok = False
            if existing_by_hash.blob_url:
                try:
                    blob_ok = AzureBlobStorage().exists(existing_by_hash.blob_url)
                except Exception:
                    blob_ok = False
            if not blob_ok:
                logger.warning(
                    f"Hash-matched Attachment {existing_by_hash.id} blob missing at "
                    f"{existing_by_hash.blob_url} — re-uploading from the downloaded content"
                )
                blob_url = self._upload_to_blob(
                    file_content=file_content,
                    file_name=file_name,
                    content_type=content_type,
                )
                self.attachment_service.update_by_public_id(
                    public_id=existing_by_hash.public_id,
                    row_version=existing_by_hash.row_version,
                    blob_url=blob_url,
                    file_size=len(file_content),
                    content_type=content_type,
                    file_extension=file_extension,
                )
                existing_by_hash = self.attachment_service.read_by_id(existing_by_hash.id)
                # Re-uploaded fresh bytes for a healed blob — queue extraction (U-187).
                self._mark_pending_extraction(existing_by_hash.id)
            # Create mapping to existing attachment
            self._create_mapping(
                attachment_id=existing_by_hash.id,
                qbo_attachable_id=qbo_attachable.id,
                qbo_id=qbo_attachable.qbo_id,
                realm_id=realm_id,
            )
            return existing_by_hash
        
        # Upload to Azure Blob Storage (use converted content/type/filename)
        blob_url = self._upload_to_blob(
            file_content=file_content,
            file_name=file_name,
            content_type=content_type,
        )
        
        # Create Attachment record
        logger.info(f"Creating new Attachment from QboAttachable {qbo_attachable.id}")
        attachment = self.attachment_service.create(
            filename=file_name,
            original_filename=qbo_attachable.file_name,
            file_extension=file_extension,
            content_type=content_type,
            file_size=len(file_content),
            file_hash=file_hash,
            blob_url=blob_url,
            description=qbo_attachable.note,
            category=qbo_attachable.category or "qbo_import",
            tags=None,
            is_archived=False,
            status="active",
            expiration_date=None,
            storage_tier="Hot",
        )
        
        # Create mapping
        self._create_mapping(
            attachment_id=attachment.id,
            qbo_attachable_id=qbo_attachable.id,
            qbo_id=qbo_attachable.qbo_id,
            realm_id=realm_id,
        )
        logger.info(f"Created mapping: Attachment {attachment.id} <-> QboAttachable {qbo_attachable.id}")

        # New source document — queue text extraction (U-187). Marked 'pending'
        # only; DI is deferred to the /admin/attachment/extract/tick sweep so this
        # ~6-min realm pull never blocks on Document Intelligence.
        self._mark_pending_extraction(attachment.id)

        return attachment

    def _resolve_mapping_state(self, *, attachment_id: int, qbo_attachable: QboAttachable):
        """
        Read-only check of the AttachableAttachment mapping table against a
        dbo-identity match, BEFORE any write happens (U-279 fast path).
        Mirrors CustomerCustomerConnector._resolve_mapping_state exactly
        (U-276/278) — see that method's docstring for the full rationale.

        Checks BOTH directions — an attachment_id-only check would miss a
        stale mapping still binding this qbo_attachable_id to a DIFFERENT
        Attachment (left behind by an earlier identity "theft" —
        SetAttachmentQboIdentity's own theft-clear UPDATE does not clean up
        the mapping table).

        NOTE (U-287): no production caller — `sync_from_qbo_*` passes these same
        accessors straight to `run_identity_fastpath`, which calls the shared
        `resolve_mapping_state` itself. Retained as the per-family test seam for the
        U-276/277/278/279 suites, which call this by name. Disposition booked in TODO.md.

        Returns (state, by_attachment, by_qbo_attachable) — see
        base.identity_fastpath.resolve_mapping_state, which owns the algorithm
        and documents the "consistent"/"missing"/"conflict" semantics (U-287);
        this is the AttachableAttachment binding of it.
        """
        return resolve_mapping_state(
            local_id=attachment_id,
            external_id=qbo_attachable.id,
            read_by_local_id=self.mapping_repo.read_by_attachment_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_attachable_id,
            external_id_attr="qbo_attachable_id",
        )

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_attachable: QboAttachable,
        dbo_attachment_id: int,
        local_side_mapping: Optional[AttachableAttachment],
        qbo_side_mapping: Optional[AttachableAttachment],
        realm_id: str,
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by
        _resolve_mapping_state. Mirrors CustomerCustomerConnector's
        identically named/shaped method — covers all three conflict shapes
        (qbo-side only, local-side only, or both) in ONE issue, never
        silently dropping either side's blocker.
        """
        parts = [
            f"Attachment identity conflict. dbo.Attachment {dbo_attachment_id} carries native "
            f"QBO identity for QboAttachable {qbo_attachable.id} (QboId={qbo_attachable.qbo_id}, "
            f"RealmId={realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboAttachable to a DIFFERENT "
                f"Attachment {qbo_side_mapping.attachment_id} (mapping {qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: Attachment {dbo_attachment_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboAttachable "
                f"{local_side_mapping.qbo_attachable_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="attachment_identity_conflict",
            entity_type="Attachment",
            entity_public_id=None,
            qbo_id=str(qbo_attachable.qbo_id) if qbo_attachable.qbo_id else None,
            realm_id=realm_id or "",
            details=" ".join(parts),
        )

    def _mark_pending_extraction(self, attachment_id: int) -> None:
        """Flag a synced Attachment for text extraction (U-187). Failure-isolated:
        a marking hiccup must never break the attachable sync (attachments are
        best-effort and the parent entity still projects)."""
        try:
            if attachment_id:
                self.attachment_service.mark_pending_extraction(attachment_id)
        except Exception as e:
            logger.warning(
                "attachable.mark_pending_extraction_failed attachment_id=%s: %s",
                attachment_id, e,
            )

    def _download_from_qbo(
        self,
        qbo_attachable: QboAttachable,
        realm_id: str,
    ) -> Optional[bytes]:
        """
        Download file content from QBO.
        
        Note: We fetch the attachable fresh from QBO to get a current TempDownloadUri,
        as the stored URI expires after a few minutes.
        
        Args:
            qbo_attachable: QboAttachable record
            realm_id: QBO realm ID
            
        Returns:
            File content as bytes, or None if download fails
        """
        # Get QBO auth
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            logger.error(f"No valid QBO auth found for realm {realm_id}")
            return None

        if not qbo_attachable.qbo_id:
            logger.error(f"QboAttachable {qbo_attachable.id} has no qbo_id")
            return None

        with QboAttachableClient(realm_id=realm_id) as client:
            # Fetch fresh attachable from QBO to get a current TempDownloadUri
            # (the stored URI expires after a few minutes)
            try:
                fresh_attachable = client.get_attachable(qbo_attachable.qbo_id)
                logger.debug(f"Fetched fresh attachable {qbo_attachable.qbo_id} for download")
            except (QboBudgetExceededError, QboWriteRefusedError):
                raise
            except Exception as e:
                logger.error(f"Failed to fetch fresh attachable {qbo_attachable.qbo_id}: {e}")
                return None
            
            return client.download_attachable(fresh_attachable)

    def _upload_to_blob(
        self,
        file_content: bytes,
        file_name: str,
        content_type: str,
    ) -> str:
        """
        Upload file to Azure Blob Storage.
        
        Args:
            file_content: File content as bytes
            file_name: Original file name
            content_type: MIME content type
            
        Returns:
            Blob URL
        """
        # Generate unique blob name using public_id only (with extension)
        public_id = str(uuid.uuid4())
        # Extract extension from file_name
        import os
        _, ext = os.path.splitext(file_name)
        blob_name = f"{public_id}{ext}" if ext else public_id
        
        storage = AzureBlobStorage()
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=file_content,
            content_type=content_type,
        )
        
        logger.debug(f"Uploaded file to blob: {blob_url}")
        return blob_url

    def _create_mapping(
        self,
        attachment_id: int,
        qbo_attachable_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> AttachableAttachment:
        """
        Create a mapping between Attachment and QboAttachable.
        """
        # Validate 1:1 constraints
        existing_by_attachment = self.mapping_repo.read_by_attachment_id(attachment_id)
        if existing_by_attachment:
            raise ValueError(
                f"Attachment {attachment_id} is already mapped to QboAttachable {existing_by_attachment.qbo_attachable_id}"
            )
        
        existing_by_qbo = self.mapping_repo.read_by_qbo_attachable_id(qbo_attachable_id)
        if existing_by_qbo:
            raise ValueError(
                f"QboAttachable {qbo_attachable_id} is already mapped to Attachment {existing_by_qbo.attachment_id}"
            )
        
        self.attachment_service.repo.set_qbo_identity(
            id=attachment_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )
        return self.mapping_repo.create(attachment_id=attachment_id, qbo_attachable_id=qbo_attachable_id)

    def _ensure_pdf_filename(self, file_name: str) -> str:
        """Ensure filename has .pdf extension (e.g. after image-to-PDF conversion)."""
        if not file_name:
            return "attachment.pdf"
        base, ext = os.path.splitext(file_name)
        return f"{base}.pdf" if base else "attachment.pdf"

    def get_mapping_by_attachment_id(self, attachment_id: int) -> Optional[AttachableAttachment]:
        """
        Get mapping by Attachment ID.
        """
        return self.mapping_repo.read_by_attachment_id(attachment_id)

    def get_mapping_by_qbo_attachable_id(self, qbo_attachable_id: int) -> Optional[AttachableAttachment]:
        """
        Get mapping by QboAttachable ID.
        """
        return self.mapping_repo.read_by_qbo_attachable_id(qbo_attachable_id)

    def sync_attachment_to_qbo(
        self,
        attachment: Attachment,
        realm_id: str,
        entity_type: str,
        entity_id: str,
    ) -> QboAttachable:
        """
        Sync a local Attachment to QuickBooks Online.
        
        This method:
        1. Checks if a mapping already exists (skip if already synced)
        2. Downloads the file from Azure Blob Storage
        3. Uploads to QBO via the upload endpoint
        4. Stores QboAttachable locally and creates mapping
        
        Args:
            attachment: Local Attachment record to sync
            realm_id: QBO realm ID for API access
            entity_type: QBO entity type to link to (e.g., "Bill")
            entity_id: QBO entity ID to link to
        
        Returns:
            QboAttachable: The local QboAttachable record created
            
        Raises:
            ValueError: If upload fails or file cannot be downloaded
        """
        attachment_id = coerce_id(attachment.id)

        # U-279 fast path: dbo-native QboId is a strong signal of "already
        # pushed" — SetAttachmentQboIdentity's theft-clear UPDATE guarantees
        # at most one row holds a given (QboId, RealmId) pair at a time, so
        # there's no "which business entity does this belong to" ambiguity
        # the way there is for the CustomerRef push case in
        # base/identity_consistency.py. But it's still corroborated against
        # the qbo.Attachable staging row below before being trusted for the
        # early return — a non-null qbo_id alone is not treated as sufficient.
        if attachment.qbo_id and (getattr(attachment, "realm_id", None) or "") == (realm_id or ""):
            qbo_attachable_repo = QboAttachableRepository()
            existing_qbo_attachable = qbo_attachable_repo.read_by_qbo_id_and_realm_id(attachment.qbo_id, realm_id)
            if existing_qbo_attachable:
                logger.info(f"Attachment {attachment_id} already carries QBO identity {attachment.qbo_id} — skipping re-upload")
                return existing_qbo_attachable
            # dbo says pushed but no matching qbo.Attachable staging row — this
            # is staging-cache lag (or the dual-write's original row was pruned),
            # not a genuine two-source conflict like the pull-side case, so it
            # falls through to the mapping-table check below as a safety net
            # rather than raising.

        # Check if already mapped (legacy path)
        existing_mapping = self.mapping_repo.read_by_attachment_id(attachment_id)
        if existing_mapping:
            logger.info(f"Attachment {attachment_id} is already mapped to QboAttachable {existing_mapping.qbo_attachable_id}")
            qbo_attachable_repo = QboAttachableRepository()
            return qbo_attachable_repo.read_by_id(existing_mapping.qbo_attachable_id)
        
        # Download file from Azure Blob Storage
        if not attachment.blob_url:
            raise ValueError(f"Attachment {attachment_id} has no blob_url")
        
        try:
            storage = AzureBlobStorage()
            file_content, metadata = storage.download_file(attachment.blob_url)
            logger.debug(f"Downloaded attachment {attachment_id} from blob: {len(file_content)} bytes")
        except AzureBlobStorageError as e:
            raise ValueError(f"Failed to download attachment from blob storage: {e}")
        
        # Get QBO auth
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")
        
        # Upload to QBO
        filename = attachment.original_filename or attachment.filename or f"attachment_{attachment.public_id}"
        content_type = attachment.content_type or metadata.get("content_type", "application/octet-stream")
        
        logger.info(f"Uploading attachment {attachment_id} to QBO: {filename} -> {entity_type} {entity_id}")

        qbo_attachable_response = None
        try:
            with QboAttachableClient(realm_id=realm_id) as client:
                qbo_attachable_response = client.upload_attachable(
                    file_content=file_content,
                    filename=filename,
                    content_type=content_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    note=attachment.description,
                )

            logger.info(f"Created QBO Attachable {qbo_attachable_response.id} for {entity_type} {entity_id}")

            # Store QboAttachable locally
            qbo_attachable_repo = QboAttachableRepository()
            local_qbo_attachable = qbo_attachable_repo.create(
                qbo_id=qbo_attachable_response.id,
                sync_token=qbo_attachable_response.sync_token,
                realm_id=realm_id,
                file_name=qbo_attachable_response.file_name,
                note=qbo_attachable_response.note,
                category=qbo_attachable_response.category,
                content_type=qbo_attachable_response.content_type,
                size=qbo_attachable_response.size,
                file_access_uri=qbo_attachable_response.file_access_uri,
                temp_download_uri=qbo_attachable_response.temp_download_uri,
                entity_ref_type=entity_type,
                entity_ref_value=entity_id,
            )

            logger.info(f"Stored local QboAttachable {local_qbo_attachable.id}")

            # Create mapping
            qbo_attachable_id = coerce_id(local_qbo_attachable.id)
            try:
                self._create_mapping(
                    attachment_id=attachment_id,
                    qbo_attachable_id=qbo_attachable_id,
                    qbo_id=qbo_attachable_response.id,
                    realm_id=realm_id,
                )
                logger.info(f"Created mapping: Attachment {attachment_id} <-> QboAttachable {qbo_attachable_id}")
            except ValueError as e:
                logger.warning(f"Could not create mapping: {e}")
                record_mapping_issue(
                    self.reconciliation_repo,
                    drift_type="attachment_mapping_orphaned",
                    entity_type="Attachment",
                    entity_public_id=str(attachment.public_id) if attachment.public_id else None,
                    qbo_id=str(qbo_attachable_response.id) if qbo_attachable_response and qbo_attachable_response.id else None,
                    realm_id=realm_id,
                    severity="critical",
                    details=(
                        f"Attachment mapping race: local Attachment public_id="
                        f"{attachment.public_id!s} id={attachment_id}, target {entity_type} {entity_id}. "
                        f"QBO Attachable {qbo_attachable_response.id} was created and is now orphaned "
                        f"(unmapped) because of a concurrent mapping race ({e}). "
                        f"It must NOT be re-uploaded — it already exists at QBO and needs manual "
                        f"mapping/dedup resolution."
                    ),
                )

            return local_qbo_attachable
        except (QboBudgetExceededError, QboWriteRefusedError):
            raise
        except Exception as exc:
            if qbo_attachable_response is not None and qbo_attachable_response.id:
                details = (
                    f"Attachment upload post-commit failure: local Attachment public_id="
                    f"{attachment.public_id!s} id={attachment_id}, target {entity_type} {entity_id}. "
                    f"QBO Attachable {qbo_attachable_response.id} was already created and must NOT be "
                    f"blindly re-uploaded — create/repair the local mapping manually. Exception: {exc!r}"
                )
            else:
                details = (
                    f"Attachment upload failed: local Attachment public_id="
                    f"{attachment.public_id!s} id={attachment_id}, target {entity_type} {entity_id}. "
                    f"Upload attempt failed and may or may not have committed server-side "
                    f"(2xx-malformed-body is POST-COMMIT-AMBIGUOUS). Exception: {exc!r}"
                ) + (f" Response detail: {exc.detail}" if getattr(exc, "detail", None) else "")
            record_mapping_issue(
                self.reconciliation_repo,
                drift_type="attachment_upload_failed",
                entity_type="Attachment",
                entity_public_id=str(attachment.public_id) if attachment.public_id else None,
                qbo_id=str(qbo_attachable_response.id) if qbo_attachable_response and qbo_attachable_response.id else None,
                realm_id=realm_id,
                severity="critical",
                details=details,
            )
            raise
