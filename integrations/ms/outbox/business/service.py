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
from shared.fanout_guard import idempotency_guards_disabled, same_attachment_id

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

    def enqueue_send_mail(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        to_addresses: list,
        cc_addresses: Optional[list] = None,
        bcc_addresses: Optional[list] = None,
        subject: str,
        body: str,
        body_type: str = "HTML",
        attachment: Optional[Dict[str, Any]] = None,
        mode: str = "draft",
        review_id: Optional[int] = None,
        bill_id: Optional[int] = None,
        forward_message_id: Optional[str] = None,
        comment_text: Optional[str] = None,
        html_preamble: Optional[str] = None,
    ) -> Optional[MsOutbox]:
        """
        Queue an outbound mail send (or draft create) for background dispatch.

        `to_addresses` / `cc_addresses` / `bcc_addresses` are lists of dicts
        shaped `{"email": str, "name": Optional[str]}` matching the existing
        mail-client `_build_recipient_list` contract — passing `address`
        instead of `email` silently drops the destination, leaving only the
        display name on the recipient line. `attachment`, when supplied,
        carries `{"name": str, "content_type": str, "content_bytes": str}`
        where `content_bytes` is **already base64-encoded** (Graph requires
        base64; storing it pre-encoded keeps the JSON payload self-sufficient
        and survives outbox retries without re-fetching the blob).

        `mode` is "draft" (default) or "send" — the worker dispatches to
        `create_draft` or `send_message` respectively.

        Forward mode: when `forward_message_id` is set, the worker dispatches
        to `create_forward_draft` (mode=draft) or `forward_message` (mode=send)
        instead, producing a draft/sent forward of the original message. The
        forward inherits subject (auto `FW: `) + body + attachments from the
        source message; `comment_text` (plain text) is prepended as the
        preamble. `subject`, `body`, and `attachment` are ignored in this
        path. Used by the review-submit notification trigger so reviewer
        replies stay in the same MS Graph conversation thread as the
        original vendor email.

        `review_id` / `bill_id` are persisted on the row for audit-trail
        backtrack: "which Review row triggered this email".
        """
        tenant_id = _resolve_tenant_id()
        if not tenant_id:
            logger.error("ms.outbox.enqueue_send_mail.no_tenant_id")
            return None
        return self.enqueue(
            kind=KIND_SEND_MAIL,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            payload={
                "to_addresses": to_addresses,
                "cc_addresses": cc_addresses or [],
                "bcc_addresses": bcc_addresses or [],
                "subject": subject,
                "body": body,
                "body_type": body_type,
                "attachment": attachment,
                "mode": mode,
                "review_id": review_id,
                "bill_id": bill_id,
                "forward_message_id": forward_message_id,
                "comment_text": comment_text,
                "html_preamble": html_preamble,
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
        downloads it at drain time.

        `attachment_id` is optional and is carried in the payload as part of
        this upload's IDENTITY (see the guard below). It is NOT a linkback:
        the worker persists nothing on success — it uploads and returns —
        so `ms.DriveItem` / `ms.DriveItemAttachment` hold no rows for these
        uploads and `DriveItemAttachmentService` has no callers. That absence
        is precisely why the guard keys on the completed outbox row: it is the
        only durable proof in the system that a SharePoint write happened.
        """
        if _writes_allowed() and not idempotency_guards_disabled():
            # Idempotency guard — SKIP requires positive proof of EVERY identity field.
            # Any missing row, NULL hash, absent id, unparseable payload, or read that
            # raises => ENQUEUE. A wrong enqueue costs one PUT; a wrong skip loses a
            # document forever. (Same asymmetry as integrations/intuit/qbo/base/sync_outcome.py.)
            try:
                completed_rows = self.repo.read_completed_by_entity(
                    entity_type=entity_type,
                    entity_public_id=entity_public_id,
                    kind=KIND_UPLOAD_SHAREPOINT_FILE,
                )
                for prior in completed_rows:
                    try:
                        parsed = json.loads(prior.payload) if prior.payload else None
                    except Exception:
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    prior_attachment = parsed.get("attachment_id")
                    # Eager evaluation — if this were lazy inside the if, a prior
                    # row with a non-numeric attachment_id would be silently skipped
                    # instead of raising, and a LATER row could then match and
                    # suppress the upload.
                    attachment_match = same_attachment_id(prior_attachment, attachment_id)
                    if (
                        parsed.get("drive_id") == drive_id
                        and parsed.get("parent_item_id") == parent_item_id
                        and parsed.get("filename") == filename
                        and parsed.get("blob_path") == blob_path
                        and attachment_match
                        # Defense-in-depth mirroring ReadCompletedMsOutboxByEntity's
                        # status='done' predicate — not a substitute for it.
                        and str(prior.status or "").lower() == "done"
                    ):
                        logger.info(
                            "ms.outbox.upload.skipped_already_uploaded",
                            extra={
                                "event_name": "ms.outbox.upload.skipped_already_uploaded",
                                "entity_type": entity_type,
                                "entity_public_id": entity_public_id,
                                "file_name": filename,
                                "outbox_public_id": prior.public_id,
                                "outcome": "skipped_already_uploaded",
                            },
                        )
                        return prior
            except Exception as error:
                logger.warning(
                    "ms.outbox.guard.read_failed",
                    extra={
                        "event_name": "ms.outbox.guard.read_failed",
                        "entity_type": entity_type,
                        "entity_public_id": entity_public_id,
                        "error_class": type(error).__name__,
                    },
                )

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
    """Look up the tenant_id from the single-tenant MsAuth record (no token refresh — this is a
    plain identity-field read, not an auth-freshness check; must not depend on Microsoft being
    reachable just to learn which tenant we're enqueueing for)."""
    try:
        from integrations.ms.auth.business.service import MsAuthService

        all_auths = MsAuthService().read_all()
        return all_auths[0].tenant_id if all_auths else None
    except Exception as error:
        logger.error(f"Error resolving tenant_id: {error}")
        return None
