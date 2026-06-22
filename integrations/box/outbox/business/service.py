# Python Standard Library Imports
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

# Local Imports
from integrations.box.base.correlation import get_correlation_id
from integrations.box.outbox.business.model import BoxOutbox
from integrations.box.outbox.persistence.repo import BoxOutboxRepository
from shared.authz.context import current_user_id

logger = logging.getLogger(__name__)


# Policy C debounce window. 5s matches QBO/MS; short enough to feel
# responsive, long enough to absorb auto-save cadence.
DEFAULT_READY_AFTER_SECONDS = 5


# Kind namespace.
KIND_UPLOAD_FILE = "upload_box_file"
KIND_UPDATE_EXCEL = "update_box_excel"  # Phase 2: constant only — no handler yet.

# `update_box_excel` operation discriminator — shared source of truth with the
# worker side (integrations/box/excel/business/service.py:OP_*). Lazy import
# to avoid pulling in openpyxl/excel-package surface at outbox load time.
from integrations.box.excel.business.service import (  # noqa: E402
    OP_STAMP_DRAW_REQUEST,
)

# The closed set of (entity_type, doc_kind) classes that may be pushed to
# Box. Enqueue refuses anything outside this set so a future call site can't
# silently start shipping a new document class without a deliberate decision.
ALLOWED_PUSH_CLASSES = frozenset(
    {
        ("bill", "attachment"),
        ("bill_credit", "attachment"),
        ("expense", "attachment"),
        ("expense", "receipt"),
        ("invoice", "packet"),
    }
)


def _writes_allowed() -> bool:
    """
    Match the BoxHttpClient write gate: enqueueing an outbox row for a write
    operation is itself a "write action" from the local-dev safety perspective.
    If ALLOW_BOX_WRITES is not 'true', we refuse to queue — logging what would
    have been queued for diagnosis. Production App Service sets the flag.
    """
    return os.getenv("ALLOW_BOX_WRITES", "").strip().lower() == "true"


class BoxOutboxService:
    """
    Service for enqueueing Box write operations into the durable outbox.

    Public surface:

        BoxOutboxService().enqueue_box_upload(
            entity_type="bill",
            entity_public_id=str(bill.public_id),
            doc_kind="attachment",
            blob_path=attachment.blob_url,
            filename=attachment.filename,
            content_type=attachment.content_type,
            box_folder_id=mapping["box_folder_id"],
            attachment_id=attachment.id,
            project_id=project_id,
        )

    Policy C coalescing is per-attachment: a duplicate enqueue for the SAME
    attachment of the same entity collapses into the existing pending row
    (debounce extended, RequestId preserved); distinct attachments of the
    same entity keep distinct rows.
    """

    def __init__(self, repo: Optional[BoxOutboxRepository] = None):
        self.repo = repo or BoxOutboxRepository()

    def enqueue_box_upload(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        doc_kind: str,
        blob_path: str,
        filename: str,
        content_type: str,
        box_folder_id: str,
        attachment_id: Optional[int] = None,
        project_id: Optional[int] = None,
        ready_after_seconds: int = DEFAULT_READY_AFTER_SECONDS,
    ) -> Optional[BoxOutbox]:
        """
        Enqueue a Box file upload for background dispatch.

        Returns the outbox row on success, or None if the enqueue was refused
        (write gate closed, or (entity_type, doc_kind) outside
        ALLOWED_PUSH_CLASSES). `blob_path` is the Azure Blob Storage path the
        worker downloads at drain time; `box_folder_id` is the Box folder id
        string from the project-folder mapping.
        """
        correlation_id = get_correlation_id()

        if not _writes_allowed():
            logger.warning(
                "box.outbox.row.refused",
                extra={
                    "event_name": "box.outbox.row.refused",
                    "correlation_id": correlation_id,
                    "operation_name": KIND_UPLOAD_FILE,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "doc_kind": doc_kind,
                    "reason": "ALLOW_BOX_WRITES_not_true",
                },
            )
            return None

        if (entity_type, doc_kind) not in ALLOWED_PUSH_CLASSES:
            logger.warning(
                "box.outbox.row.refused",
                extra={
                    "event_name": "box.outbox.row.refused",
                    "correlation_id": correlation_id,
                    "operation_name": KIND_UPLOAD_FILE,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "doc_kind": doc_kind,
                    "reason": "push_class_not_allowed",
                },
            )
            return None

        # Deterministic identity-embedded name: re-runs of the same enqueue
        # produce the same filename, which is what makes the conflict-recovery
        # path in BoxFileService.push_blob_to_box idempotent.
        # Lazy import: the file package owns naming; importing at module load
        # would couple outbox/ -> file/ at import time.
        from integrations.box.file.business.naming import sanitize_filename

        safe_filename = sanitize_filename(filename, entity_public_id)

        now = datetime.now(timezone.utc)
        ready_after = now + timedelta(seconds=ready_after_seconds)
        payload = {
            "blob_path": blob_path,
            "filename": safe_filename,
            "content_type": content_type,
            "box_folder_id": box_folder_id,
            "attachment_id": attachment_id,
            "doc_kind": doc_kind,
            "project_id": project_id,
        }

        # Policy C coalesce — per-attachment, not per-entity: one entity can
        # legitimately have several pending uploads (one per attachment).
        # Only the row whose payload attachment_id matches collapses; its
        # debounce window extends and its RequestId is preserved so retries
        # of the existing row stay recognizable in logs.
        existing = self._find_coalescible(
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            attachment_id=attachment_id,
        )
        if existing:
            updated = self.repo.update_ready_after(
                id=existing.id,
                row_version=existing.row_version,
                ready_after=ready_after,
            )
            logger.info(
                "box.outbox.row.coalesced",
                extra={
                    "event_name": "box.outbox.row.coalesced",
                    "correlation_id": correlation_id,
                    "operation_name": KIND_UPLOAD_FILE,
                    "outbox_public_id": existing.public_id,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "doc_kind": doc_kind,
                    "attachment_id": attachment_id,
                    "new_ready_after": ready_after.isoformat(),
                },
            )
            return updated or existing

        request_id = str(uuid.uuid4())
        created = self.repo.create(
            kind=KIND_UPLOAD_FILE,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            request_id=request_id,
            payload=json.dumps(payload),
            ready_after=ready_after,
            correlation_id=correlation_id,
            # None → CreateBoxOutbox sproc COALESCEs to 17 (system default).
            created_by_user_id=current_user_id.get(),
        )
        logger.info(
            "box.outbox.row.enqueued",
            extra={
                "event_name": "box.outbox.row.enqueued",
                "correlation_id": correlation_id,
                "operation_name": KIND_UPLOAD_FILE,
                "outbox_public_id": created.public_id,
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "doc_kind": doc_kind,
                "attachment_id": attachment_id,
                "request_id": request_id,
                "ready_after": ready_after.isoformat(),
            },
        )
        return created

    def enqueue_box_excel(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        project_id: int,
        box_file_id: str,
        worksheet_name: str,
    ) -> Optional[BoxOutbox]:
        """
        Enqueue a Box DETAILS-tab Excel update for background dispatch (Phase 3).

        Returns the outbox row on success, or None if the enqueue was refused
        (write gate closed). Gated on ALLOW_BOX_WRITES exactly like
        enqueue_box_upload — queueing a write is itself a "write action" from
        the local-dev safety perspective.

        NO Policy-C coalesce in v1: one row per (entity, workbook). Idempotency
        is guaranteed downstream by column-Z (the DETAILS reconciliation key) —
        the drain handler skips any line item whose public_id is already present
        and no-ops if all are, so re-runs are safe even without debounce
        collapsing.

        v1 tradeoff (documented): this churns one Box file version per completed
        entity per workbook. Batch-apply-per-workbook (one version per drain
        pass across many entities) is a future optimization — it would require a
        coalesce/aggregation layer the col-Z idempotency makes unnecessary for
        correctness, only efficiency.

        Payload JSON:
          {"box_file_id","worksheet_name","entity_type","entity_public_id",
           "project_id"}
        """
        correlation_id = get_correlation_id()

        if not _writes_allowed():
            logger.warning(
                "box.outbox.row.refused",
                extra={
                    "event_name": "box.outbox.row.refused",
                    "correlation_id": correlation_id,
                    "operation_name": KIND_UPDATE_EXCEL,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "reason": "ALLOW_BOX_WRITES_not_true",
                },
            )
            return None

        now = datetime.now(timezone.utc)
        ready_after = now + timedelta(seconds=DEFAULT_READY_AFTER_SECONDS)
        payload = {
            "box_file_id": box_file_id,
            "worksheet_name": worksheet_name,
            "entity_type": entity_type,
            "entity_public_id": entity_public_id,
            "project_id": project_id,
        }

        request_id = str(uuid.uuid4())
        created = self.repo.create(
            kind=KIND_UPDATE_EXCEL,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            request_id=request_id,
            payload=json.dumps(payload),
            ready_after=ready_after,
            correlation_id=correlation_id,
            # None → CreateBoxOutbox sproc COALESCEs to 17 (system default).
            created_by_user_id=current_user_id.get(),
        )
        logger.info(
            "box.outbox.row.enqueued",
            extra={
                "event_name": "box.outbox.row.enqueued",
                "correlation_id": correlation_id,
                "operation_name": KIND_UPDATE_EXCEL,
                "outbox_public_id": created.public_id,
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "box_file_id": box_file_id,
                "worksheet_name": worksheet_name,
                "project_id": project_id,
                "request_id": request_id,
                "ready_after": ready_after.isoformat(),
            },
        )
        return created

    def enqueue_box_excel_draw_stamp(
        self,
        *,
        invoice_public_id: str,
        project_id: int,
        box_file_id: str,
        worksheet_name: str,
    ) -> Optional[BoxOutbox]:
        """
        Enqueue a Box DETAILS-tab UPDATE (column H = DRAW REQUEST) for an
        invoice's source line items.

        Sibling of enqueue_box_excel, but the worker treats this row as a
        stamp-only operation: no inserts, only column-H writes on rows
        already in DETAILS (matched by col-Z public_id). The MS / SharePoint
        side already runs InvoiceService.sync_to_excel_workbook for the same
        purpose against the SP-hosted workbook; this is the Box mirror.

        Payload JSON (mirrors enqueue_box_excel + an `operation` discriminator):
          {"operation":"stamp_draw_request","box_file_id","worksheet_name",
           "entity_type":"invoice","entity_public_id":<invoice_public_id>,
           "project_id"}

        Gated on ALLOW_BOX_WRITES like its siblings. Returns the row, or None
        if refused.
        """
        correlation_id = get_correlation_id()

        if not _writes_allowed():
            logger.warning(
                "box.outbox.row.refused",
                extra={
                    "event_name": "box.outbox.row.refused",
                    "correlation_id": correlation_id,
                    "operation_name": KIND_UPDATE_EXCEL,
                    "entity_type": "invoice",
                    "entity_public_id": invoice_public_id,
                    "operation": OP_STAMP_DRAW_REQUEST,
                    "reason": "ALLOW_BOX_WRITES_not_true",
                },
            )
            return None

        now = datetime.now(timezone.utc)
        ready_after = now + timedelta(seconds=DEFAULT_READY_AFTER_SECONDS)
        payload = {
            "operation": OP_STAMP_DRAW_REQUEST,
            "box_file_id": box_file_id,
            "worksheet_name": worksheet_name,
            "entity_type": "invoice",
            "entity_public_id": invoice_public_id,
            "project_id": project_id,
        }

        request_id = str(uuid.uuid4())
        created = self.repo.create(
            kind=KIND_UPDATE_EXCEL,
            entity_type="invoice",
            entity_public_id=invoice_public_id,
            request_id=request_id,
            payload=json.dumps(payload),
            ready_after=ready_after,
            correlation_id=correlation_id,
            created_by_user_id=current_user_id.get(),
        )
        logger.info(
            "box.outbox.row.enqueued",
            extra={
                "event_name": "box.outbox.row.enqueued",
                "correlation_id": correlation_id,
                "operation_name": KIND_UPDATE_EXCEL,
                "operation": OP_STAMP_DRAW_REQUEST,
                "outbox_public_id": created.public_id,
                "entity_type": "invoice",
                "entity_public_id": invoice_public_id,
                "box_file_id": box_file_id,
                "worksheet_name": worksheet_name,
                "project_id": project_id,
                "request_id": request_id,
                "ready_after": ready_after.isoformat(),
            },
        )
        return created

    def enqueue_box_excel_batch(
        self,
        *,
        entities: list,
        project_id: int,
        box_file_id: str,
        worksheet_name: str,
    ) -> Optional[BoxOutbox]:
        """
        Batch variant of enqueue_box_excel: ONE outbox row whose payload carries
        MANY entities for the SAME workbook. The drain handler downloads the
        .xlsx once, builds + appends rows for every entity, and uploads a SINGLE
        new version — instead of one download/edit/upload (one Box file version)
        per entity. Used by the QBO pull, which routinely projects many
        bills/expenses/credits onto one project's workbook in a single run.

        `entities` is a list of {"entity_type", "entity_public_id"} dicts. The
        drain re-fetches each entity fresh and dedups by column-Z, exactly like
        the single-entity path — only the I/O is coalesced.

        Gated on ALLOW_BOX_WRITES. Returns the row, or None if refused / empty.

        Payload JSON:
          {"box_file_id","worksheet_name","project_id",
           "entities":[{"entity_type","entity_public_id"},...]}
        """
        correlation_id = get_correlation_id()

        if not entities:
            return None

        if not _writes_allowed():
            logger.warning(
                "box.outbox.row.refused",
                extra={
                    "event_name": "box.outbox.row.refused",
                    "correlation_id": correlation_id,
                    "operation_name": KIND_UPDATE_EXCEL,
                    "entity_type": "box_excel_batch",
                    "entity_public_id": str(project_id),
                    "reason": "ALLOW_BOX_WRITES_not_true",
                },
            )
            return None

        now = datetime.now(timezone.utc)
        ready_after = now + timedelta(seconds=DEFAULT_READY_AFTER_SECONDS)
        payload = {
            "box_file_id": box_file_id,
            "worksheet_name": worksheet_name,
            "project_id": project_id,
            "entities": entities,
        }

        request_id = str(uuid.uuid4())
        # The entity_public_id column is a per-row UNIQUEIDENTIFIER; a batch row
        # represents many entities, so it carries its own synthetic id (the real
        # entity refs live in payload["entities"]).
        created = self.repo.create(
            kind=KIND_UPDATE_EXCEL,
            entity_type="box_excel_batch",
            entity_public_id=str(uuid.uuid4()),
            request_id=request_id,
            payload=json.dumps(payload),
            ready_after=ready_after,
            correlation_id=correlation_id,
            created_by_user_id=current_user_id.get(),
        )
        logger.info(
            "box.outbox.row.enqueued",
            extra={
                "event_name": "box.outbox.row.enqueued",
                "correlation_id": correlation_id,
                "operation_name": KIND_UPDATE_EXCEL,
                "outbox_public_id": created.public_id,
                "entity_type": "box_excel_batch",
                "entity_count": len(entities),
                "box_file_id": box_file_id,
                "worksheet_name": worksheet_name,
                "project_id": project_id,
                "request_id": request_id,
                "ready_after": ready_after.isoformat(),
            },
        )
        return created

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _find_coalescible(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        attachment_id: Optional[int],
    ) -> Optional[BoxOutbox]:
        """
        Find a pending/failed `upload_box_file` row for the same entity whose
        payload `attachment_id` matches. Rows with unparsable payloads are
        skipped (they'll dead-letter at drain time on their own).
        """
        candidates = self.repo.read_pending_by_entity(
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            kind=KIND_UPLOAD_FILE,
        )
        for candidate in candidates:
            if not candidate.payload:
                continue
            try:
                candidate_payload = json.loads(candidate.payload)
            except (ValueError, TypeError):
                continue
            if not isinstance(candidate_payload, dict):
                continue
            if candidate_payload.get("attachment_id") == attachment_id:
                return candidate
        return None
