# Python Standard Library Imports
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

# Local Imports
from integrations.ms.base.correlation import get_correlation_id
from integrations.ms.base.errors import MsWriteRefusedError
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.persistence.repo import MsOutboxRepository

logger = logging.getLogger(__name__)


# Policy C debounce window. 5s matches QBO; short enough to feel responsive,
# long enough to absorb auto-save cadence.
DEFAULT_DEBOUNCE_SECONDS = 5


# Kind namespace.
KIND_UPLOAD_SHAREPOINT_FILE = "upload_sharepoint_file"
KIND_APPEND_EXCEL_ROW = "append_excel_row"
KIND_INSERT_EXCEL_ROW = "insert_excel_row"
KIND_SEND_MAIL = "send_mail"  # Phase 4

# Per the Round 0 decision: only uploads coalesce (duplicate enqueues for the
# same attachment should collapse). Excel rows NEVER coalesce — each bill is
# a distinct row; two enqueues for the same bill must not collapse into one.
# Mail sends also don't coalesce (don't want to risk losing an intended send).
_COALESCING_KINDS: Set[str] = {
    KIND_UPLOAD_SHAREPOINT_FILE,
}


def _writes_allowed() -> bool:
    """
    Match the MsGraphClient write gate: enqueueing an outbox row for a write
    operation is itself a "write action" from the local-dev safety perspective.
    If ALLOW_MS_WRITES is not 'true', we refuse to queue — logging what would
    have been queued for diagnosis. Production App Service sets the flag.
    """
    return os.getenv("ALLOW_MS_WRITES", "").strip().lower() == "true"


class MsOutboxService:
    """
    Service for enqueueing MS Graph write operations into the durable outbox.

    Public surface:

        MsOutboxService().enqueue(
            kind="append_excel_row",
            entity_type="Bill",
            entity_public_id=bill.public_id,
            tenant_id=tenant_id,
        )

    Per-Kind coalescing: `upload_sharepoint_file` coalesces (Policy C), other
    Kinds always create fresh rows. `payload` is a per-row JSON dict the
    worker's handler understands — for Excel rows it carries the actual row
    values; for uploads it tracks upload-session state after the first chunk.
    """

    def __init__(self, repo: Optional[MsOutboxRepository] = None):
        self.repo = repo or MsOutboxRepository()

    def enqueue(
        self,
        *,
        kind: str,
        entity_type: str,
        entity_public_id: str,
        tenant_id: str,
        payload: Optional[Dict[str, Any]] = None,
        debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
    ) -> Optional[MsOutbox]:
        """
        Enqueue an MS Graph write operation.

        Returns the outbox row on success, or None if the write was refused
        by the local-dev gate. Callers that need to distinguish refused vs
        enqueued should check `ALLOW_MS_WRITES` themselves or check the
        return value.
        """
        correlation_id = get_correlation_id()

        if not _writes_allowed():
            logger.warning(
                "ms.outbox.row.refused",
                extra={
                    "event_name": "ms.outbox.row.refused",
                    "correlation_id": correlation_id,
                    "operation_name": kind,
                    "tenant_id": tenant_id,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "reason": "ALLOW_MS_WRITES_not_true",
                },
            )
            return None

        now = datetime.now(timezone.utc)
        ready_after = now + timedelta(seconds=debounce_seconds)
        payload_json = json.dumps(payload) if payload is not None else None

        if kind in _COALESCING_KINDS:
            existing = self.repo.read_pending_by_entity(
                entity_type=entity_type,
                entity_public_id=entity_public_id,
                kind=kind,
            )
            if existing:
                # Extend debounce. Don't touch RequestId (so Graph still
                # dedups) or Payload (we trust the stored state for the
                # current attempt).
                updated = self.repo.update_ready_after(
                    id=existing.id,
                    row_version=existing.row_version,
                    ready_after=ready_after,
                )
                logger.info(
                    "ms.outbox.row.coalesced",
                    extra={
                        "event_name": "ms.outbox.row.coalesced",
                        "correlation_id": correlation_id,
                        "operation_name": kind,
                        "tenant_id": tenant_id,
                        "outbox_public_id": existing.public_id,
                        "entity_type": entity_type,
                        "entity_public_id": entity_public_id,
                        "new_ready_after": ready_after.isoformat(),
                    },
                )
                return updated or existing

        request_id = str(uuid.uuid4())
        created = self.repo.create(
            kind=kind,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            request_id=request_id,
            payload=payload_json,
            ready_after=ready_after,
            correlation_id=correlation_id,
        )
        logger.info(
            "ms.outbox.row.enqueued",
            extra={
                "event_name": "ms.outbox.row.enqueued",
                "correlation_id": correlation_id,
                "operation_name": kind,
                "tenant_id": tenant_id,
                "outbox_public_id": created.public_id,
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "request_id": request_id,
                "ready_after": ready_after.isoformat(),
            },
        )
        return created

    # ------------------------------------------------------------------ #
    # Convenience methods for callers that don't want to build payload /
    # pick the right Kind by hand. These are the supported entry points
    # used by BillService / ExpenseService completion pipelines.
    # ------------------------------------------------------------------ #

    def enqueue_excel_append(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        drive_id: str,
        item_id: str,
        worksheet_name: str,
        values: list,
        session_id: Optional[str] = None,
    ) -> Optional[MsOutbox]:
        """Queue an `append_excel_rows` call for background dispatch."""
        tenant_id = _resolve_tenant_id()
        if not tenant_id:
            logger.error("ms.outbox.enqueue_excel_append.no_tenant_id")
            return None
        return self.enqueue(
            kind=KIND_APPEND_EXCEL_ROW,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            payload={
                "drive_id": drive_id,
                "item_id": item_id,
                "worksheet_name": worksheet_name,
                "values": values,
                "session_id": session_id,
            },
        )

    def enqueue_excel_insert(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        drive_id: str,
        item_id: str,
        worksheet_name: str,
        row_index: int,
        values: list,
        session_id: Optional[str] = None,
    ) -> Optional[MsOutbox]:
        """Queue an `insert_excel_rows` call for background dispatch."""
        tenant_id = _resolve_tenant_id()
        if not tenant_id:
            logger.error("ms.outbox.enqueue_excel_insert.no_tenant_id")
            return None
        return self.enqueue(
            kind=KIND_INSERT_EXCEL_ROW,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            payload={
                "drive_id": drive_id,
                "item_id": item_id,
                "worksheet_name": worksheet_name,
                "row_index": row_index,
                "values": values,
                "session_id": session_id,
            },
        )

    def enqueue_sharepoint_upload(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        drive_id: str,
        parent_item_id: str,
        filename: str,
        content_type: str,
        blob_path: str,
        attachment_id: Optional[int] = None,
    ) -> Optional[MsOutbox]:
        """
        Queue a SharePoint upload for background dispatch. `blob_path` is the
        Azure Blob Storage URL/path where the content lives; the worker
        downloads it at drain time. `attachment_id` is optional — when
        supplied, the worker links the uploaded DriveItem back to the
        Attachment record after a successful upload.
        """
        tenant_id = _resolve_tenant_id()
        if not tenant_id:
            logger.error("ms.outbox.enqueue_sharepoint_upload.no_tenant_id")
            return None
        return self.enqueue(
            kind=KIND_UPLOAD_SHAREPOINT_FILE,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            payload={
                "drive_id": drive_id,
                "parent_item_id": parent_item_id,
                "filename": filename,
                "content_type": content_type,
                "blob_path": blob_path,
                "attachment_id": attachment_id,
            },
        )


def _resolve_tenant_id() -> Optional[str]:
    """Look up the tenant_id from the single-tenant MsAuth record."""
    try:
        from integrations.ms.auth.business.service import MsAuthService

        auth = MsAuthService().ensure_valid_token()
        return auth.tenant_id if auth else None
    except Exception as error:
        logger.error(f"Error resolving tenant_id: {error}")
        return None
