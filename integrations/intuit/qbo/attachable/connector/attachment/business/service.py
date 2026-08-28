# Python Standard Library Imports
import logging
import os
import uuid
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.attachable.business.model import QboAttachable
from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.base.identity_fastpath import (
    run_identity_fastpath_dbo_only,
    stamp_dbo_identity_with_lock,
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
        attachment_service: Optional[AttachmentService] = None,
        auth_service: Optional[QboAuthService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the AttachableAttachmentConnector."""
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
        1. Resolves identity dbo-only (U-300b) via
           `run_identity_fastpath_dbo_only` against dbo.Attachment's native
           QboId/RealmId (U-238c) -- no `qbo.AttachableAttachment` mapping-
           table hop left to fall back to (Sec.15 of
           docs/staging_removal_phase4_5_scoping.md: every valid mapping row
           already has a matching dbo stamp, so the legacy fallback this
           unit removes was already dead in practice)
        2. Downloads the file from QBO if needed
        3. Uploads to Azure Blob Storage
        4. Creates or updates the Attachment accordingly

        Args:
            qbo_attachable: transient (never persisted) QboAttachable built
                by QboAttachableService._upsert_attachable from the QBO pull
                response
            realm_id: QBO realm ID for API access

        Returns:
            Attachment: The synced Attachment record
        """
        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_attachable.qbo_id,
            realm_id=realm_id,
            entity_label="Attachment",
            external_label="QboAttachable",
            lock_resource_label="Attachment",
            read_direct_by_qbo_identity=self.attachment_service.read_by_qbo_identity,
            apply_fields=lambda row: self._verify_or_heal_pulled_blob(row, qbo_attachable, realm_id),
            resolve_candidate=lambda: self._resolve_pulled_attachment_candidate(qbo_attachable, realm_id),
            stamp_identity=lambda candidate: self._stamp_pulled_identity(
                attachment_id=coerce_id(candidate.id),
                qbo_id=qbo_attachable.qbo_id,
                realm_id=realm_id,
            ),
        )
        if outcome.entity is None:
            # U-316: no longer race-reachable (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a
            # directly-invoked falsy qbo_attachable.qbo_id (this public
            # method has no guard of its own; pinned by
            # test_no_qbo_id_raises_without_ever_downloading). The
            # production pull path already guards this upstream via
            # QboAttachableService._upsert_attachable.
            raise RuntimeError(
                f"Failed to resolve Attachment for QboAttachable {qbo_attachable.id} "
                f"(qbo_id={qbo_attachable.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _verify_or_heal_pulled_blob(
        self,
        attachment: Attachment,
        qbo_attachable: QboAttachable,
        realm_id: str,
    ) -> Optional[Attachment]:
        """
        `apply_fields` for the dbo-only fast path's HIT branch (a direct or
        race-discovered dbo.Attachment already holds this identity, U-300b):
        verify its blob is still present and heal (re-download + re-upload)
        if it went missing. The identity-mismatch corrective re-stamp the
        pre-U-300b version of this block also performed is dropped -- a hit
        under `run_identity_fastpath_dbo_only` is, by construction, already
        the exact (qbo_id, realm_id) pair (`read_by_qbo_identity` matches on
        both columns), so that branch could never fire.
        """
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
            return attachment

        # Blob is missing — re-download from QBO and re-upload
        file_content = self._download_from_qbo(qbo_attachable, realm_id)
        if not file_content:
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

        content_type = qbo_attachable.content_type or "application/octet-stream"
        file_name = qbo_attachable.file_name or f"attachment_{qbo_attachable.qbo_id}"
        file_content, content_type, file_extension = ensure_pdf(file_content, content_type, file_name)
        if file_extension == ".pdf":
            file_name = self._ensure_pdf_filename(file_name)
            file_content = compact_pdf(file_content)
        return self._upload_and_heal_blob(
            attachment=attachment,
            file_content=file_content,
            file_name=file_name,
            content_type=content_type,
            file_extension=file_extension,
        )

    def _upload_and_heal_blob(
        self,
        *,
        attachment: Attachment,
        file_content: bytes,
        file_name: str,
        content_type: str,
        file_extension: str,
    ) -> Optional[Attachment]:
        """
        Shared blob-heal tail for both the HIT path (`_verify_or_heal_pulled_
        blob`, already-downloaded fresh bytes) and the hash-dedupe branch of
        the MISS path (`_resolve_pulled_attachment_candidate`, bytes already
        downloaded earlier in that same call): upload, point the Attachment's
        blob_url at it, mark pending extraction, return the refreshed row.
        """
        blob_url = self._upload_to_blob(
            file_content=file_content,
            file_name=file_name,
            content_type=content_type,
        )
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
        return self.attachment_service.read_by_id(attachment.id)

    def _resolve_pulled_attachment_candidate(
        self,
        qbo_attachable: QboAttachable,
        realm_id: str,
    ) -> Attachment:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-300b):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.Attachment currently holds this
        identity, including the re-read under lock). Finds-or-creates the
        local Attachment to bind — hash-dedupe against existing content first,
        else download + upload + create fresh. Returns the row for
        `stamp_identity` to bind; never itself calls set_qbo_identity or
        writes a `qbo.AttachableAttachment` mapping row.
        """
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

        # Check for duplicate by hash. NOTE: ReadAttachmentByHash's projection does
        # NOT select QboId/RealmId (only ReadAttachmentById/ByPublicId/
        # ByQboIdAndRealmId do — see AttachmentRepository._from_db's own comment),
        # so `existing_by_hash.qbo_id` is unconditionally None here regardless of
        # the row's real identity — an unreliable read, not a guard. The real
        # identity-theft guard lives in _stamp_pulled_identity's pre-stamp re-read
        # (via read_by_id, which DOES carry QboId/RealmId), the shared choke point
        # both this path and the fresh-create path funnel through.
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
                existing_by_hash = self._upload_and_heal_blob(
                    attachment=existing_by_hash,
                    file_content=file_content,
                    file_name=file_name,
                    content_type=content_type,
                    file_extension=file_extension,
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

        # New source document — queue text extraction (U-187). Marked 'pending'
        # only; DI is deferred to the /admin/attachment/extract/tick sweep so this
        # ~6-min realm pull never blocks on Document Intelligence.
        self._mark_pending_extraction(attachment.id)

        return attachment

    def _stamp_pulled_identity(self, *, attachment_id: int, qbo_id: str, realm_id: str) -> Optional[Attachment]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-300b),
        delegating the row-scoped lock + theft-guard + write sequence to the
        shared `stamp_dbo_identity_with_lock` (U-328/U-331 —
        `docs/design/stamp-lock-helper.md`) — see that function's own
        docstring for why a SECOND lock, keyed on the CANDIDATE's
        attachment_id rather than the qbo_id/realm_id
        `run_identity_fastpath_dbo_only` already locks on, is needed here:
        two concurrent pulls for two DIFFERENT QboAttachables that
        hash-dedupe onto the SAME Attachment acquire two DIFFERENT
        qbo_id-keyed locks upstream (no contention there), so without this
        second lock the second racer's pre-check could read stale state and
        silently steal the identity.

        No `apply_fields` — Attachment has no QBO-derived field to write at
        stamp time (the blob-heal happens on the fast path's HIT branch, via
        `_verify_or_heal_pulled_blob`, not here). `on_conflict` records the
        stamp-time race as a `ReconciliationIssue` (Decision 2, U-331) —
        Attachment had no such recording before this unit; Customer/Project/
        Vendor already did.
        """
        return stamp_dbo_identity_with_lock(
            candidate_id=attachment_id,
            entity_label="Attachment",
            qbo_id=qbo_id,
            realm_id=realm_id,
            read_by_id=self.attachment_service.read_by_id,
            write_identity=lambda c: self.attachment_service.repo.set_qbo_identity(
                id=c.id, qbo_id=qbo_id, realm_id=realm_id,
            ),
            on_conflict=lambda c: self._raise_duplicate_qbo_attachment_issue(
                attachment_id=attachment_id, qbo_id=qbo_id, realm_id=realm_id,
                local_attachment=c, existing_qbo_id=c.qbo_id,
            ),
        )

    def _raise_duplicate_qbo_attachment_issue(
        self, *, attachment_id: int, qbo_id: str, realm_id: str,
        local_attachment: Attachment, existing_qbo_id: str,
    ) -> None:
        """
        Record a stamp-time theft-guard trip on qbo.ReconciliationIssue
        (Decision 2, U-328/U-331) — closes the recording asymmetry
        `stamp-lock-helper.md` D2 flagged: Customer/Project/Vendor already
        recorded this class of race at stamp time, Attachment did not. Uses
        the dedicated `attachment_identity_conflict` DriftType (registered in
        drift_types.py, previously unused) rather than the push-side
        `attachment_mapping_orphaned`/`attachment_upload_failed` types, which
        describe a different failure shape (a push-side race, not a pull-side
        hash-dedupe candidate race). No existing pull-side resolve-time
        recorder to unify with here (unlike CostCode/SubCostCode's reused
        `_raise_duplicate_qbo_item_issue`) — Attachment's `resolve_candidate`
        (hash-dedupe) has no side-channel duplicate-identity check of its own.
        """
        details = (
            f"Attachment stamp-time identity conflict. Candidate Attachment {attachment_id} "
            f"already carries QboId={existing_qbo_id} (realm "
            f"{getattr(local_attachment, 'realm_id', None)!r}) and cannot be re-stamped with "
            f"qbo_id={qbo_id} realm_id={realm_id}. Resolve by merging or restoring the correct mapping."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="attachment_identity_conflict",
            entity_type="Attachment",
            entity_public_id=(
                str(local_attachment.public_id) if getattr(local_attachment, "public_id", None) else None
            ),
            qbo_id=str(qbo_id) if qbo_id else None,
            realm_id=realm_id or "",
            details=details,
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

    def _stamp_pushed_identity(self, *, attachment_id: int, qbo_id: str, realm_id: str) -> None:
        """
        Stamp dbo.Attachment's native QboId/RealmId directly after a
        successful QBO Attachable upload (U-285 push-side retire — the
        push path's sole bookkeeping write, replacing the old
        qbo.Attachable-row-create + `_create_mapping` pair).

        Race-guards the same case `_create_mapping`'s uniqueness pre-check
        used to catch: two Bills sharing the same physical Attachment,
        drained concurrently, can both pass the top-of-function "not yet
        pushed" check before either finishes uploading to QBO. Re-reads the
        Attachment's CURRENT identity right before stamping — if a
        concurrent push already claimed it, raise so the caller records the
        now-orphaned upload as a critical reconciliation issue instead of
        silently overwriting the winning identity with this losing one.
        """
        current = self.attachment_service.read_by_id(attachment_id)
        if current and current.qbo_id and (getattr(current, "realm_id", None) or "") == (realm_id or ""):
            raise ValueError(
                f"Attachment {attachment_id} is already mapped to QboAttachable {current.qbo_id}"
            )
        self.attachment_service.repo.set_qbo_identity(
            id=attachment_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )

    def _transient_attachable_from_response(
        self,
        *,
        response,
        realm_id: str,
        entity_type: str,
        entity_id: str,
    ) -> QboAttachable:
        """
        Build an in-memory (never persisted) QboAttachable from the QBO
        upload response, for callers that expect this method's historical
        return type. U-285: the push path no longer creates a qbo.Attachable
        row to back it, so `id`/`public_id`/`row_version`/timestamps are None.
        """
        return QboAttachable(
            id=None,
            public_id=None,
            row_version=None,
            created_datetime=None,
            modified_datetime=None,
            qbo_id=response.id,
            sync_token=response.sync_token,
            realm_id=realm_id,
            file_name=response.file_name,
            note=response.note,
            category=response.category,
            content_type=response.content_type,
            size=response.size,
            file_access_uri=response.file_access_uri,
            temp_download_uri=response.temp_download_uri,
            entity_ref_type=entity_type,
            entity_ref_value=entity_id,
        )

    def _transient_attachable_from_dbo(
        self,
        attachment: Attachment,
        realm_id: str,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> QboAttachable:
        """
        Build an in-memory (never persisted) QboAttachable from an Attachment
        that dbo.Attachment already shows as pushed (U-285 push-path
        idempotency fast path — no legacy qbo.Attachable/AttachableAttachment
        row exists to read, so there is no QBO API response to build from
        either; this call skips the QBO API entirely). `id`/`public_id`/
        `row_version`/timestamps are None, matching
        `_transient_attachable_from_response`'s contract for the fresh-push
        case.
        """
        return QboAttachable(
            id=None,
            public_id=None,
            row_version=None,
            created_datetime=None,
            modified_datetime=None,
            qbo_id=attachment.qbo_id,
            sync_token=None,
            realm_id=realm_id,
            file_name=attachment.original_filename or attachment.filename,
            note=attachment.description,
            category=attachment.category,
            content_type=attachment.content_type,
            size=attachment.file_size,
            file_access_uri=None,
            temp_download_uri=None,
            entity_ref_type=entity_type,
            entity_ref_value=entity_id,
        )

    def _ensure_pdf_filename(self, file_name: str) -> str:
        """Ensure filename has .pdf extension (e.g. after image-to-PDF conversion)."""
        if not file_name:
            return "attachment.pdf"
        base, ext = os.path.splitext(file_name)
        return f"{base}.pdf" if base else "attachment.pdf"

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
        1. Checks if this Attachment was already pushed via dbo.Attachment's
           own QboId/RealmId alone (U-285 stopped the push from writing the
           legacy qbo.Attachable / qbo.AttachableAttachment tables; U-300c-prereq
           removed the now-inert reads of them — see point 4)
        2. Downloads the file from Azure Blob Storage
        3. Uploads to QBO via the upload endpoint
        4. Stamps dbo.Attachment's native QboId/RealmId directly (U-285:
           the push path no longer stages a qbo.Attachable row or a
           qbo.AttachableAttachment mapping row — dbo.Attachment identity is
           the sole record that this Attachment was pushed. The pull side
           still populates qbo.Attachable for now; repointing it is a
           follow-up unit, see docs/staging_removal_phase4_5_scoping.md §6/§15)

        Args:
            attachment: Local Attachment record to sync
            realm_id: QBO realm ID for API access
            entity_type: QBO entity type to link to (e.g., "Bill")
            entity_id: QBO entity ID to link to

        Returns:
            QboAttachable: the QBO-side record. For a fresh push, or a retry of
            an Attachment this method already pushed itself (dbo.Attachment
            already carries QboId/RealmId), it is a transient (never persisted)
            QboAttachable — `id`/`public_id`/`row_version`/timestamps are None
            since no local row backs it.

        Raises:
            ValueError: If upload fails or file cannot be downloaded
        """
        attachment_id = coerce_id(attachment.id)

        # U-300c-prereq: dbo.Attachment's own QboId/RealmId is the SOLE
        # identity store for the push path. The legacy qbo.Attachable /
        # qbo.AttachableAttachment corroboration reads that used to gate this
        # early return were removed once (a) U-285 stopped the push from
        # writing either table and (b) a live check proved 0 legacy mapping
        # rows whose dbo.Attachment.QboId is NULL — i.e. the only branch where
        # a legacy read could change behavior (a mapping hit while dbo identity
        # is absent) is unreachable in practice. See docs/design/u300c.md
        # §3.1.2. SetAttachmentQboIdentity's theft-clear UPDATE guarantees at
        # most one row holds a given (QboId, RealmId) pair, so a non-null
        # qbo_id with a matching realm is a sufficient "already pushed" signal
        # on its own; _stamp_pushed_identity's own race guard covers the push
        # path below.
        already_dbo_pushed = bool(
            attachment.qbo_id and (getattr(attachment, "realm_id", None) or "") == (realm_id or "")
        )
        if already_dbo_pushed:
            logger.info(
                f"Attachment {attachment_id} already carries QBO identity {attachment.qbo_id} "
                f"— skipping re-upload (dbo-native identity, U-300c-prereq)"
            )
            return self._transient_attachable_from_dbo(
                attachment, realm_id, entity_type=entity_type, entity_id=entity_id,
            )

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

            # U-285 (Phase-5 push-side retire): stamp identity directly on
            # dbo.Attachment — no qbo.Attachable staging row, no
            # qbo.AttachableAttachment mapping row.
            try:
                self._stamp_pushed_identity(
                    attachment_id=attachment_id,
                    qbo_id=qbo_attachable_response.id,
                    realm_id=realm_id,
                )
                logger.info(
                    f"Stamped QBO identity on Attachment {attachment_id} "
                    f"<- QboAttachable {qbo_attachable_response.id}"
                )
            except ValueError as e:
                logger.warning(f"Could not stamp identity: {e}")
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

            return self._transient_attachable_from_response(
                response=qbo_attachable_response,
                realm_id=realm_id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
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
