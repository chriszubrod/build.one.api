# Python Standard Library Imports
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

# Local Imports
from integrations.ms.base.correlation import (
    ensure_correlation_id,
    idempotency_key_context,
    set_correlation_id,
)
from integrations.ms.base.errors import MsGraphError
from integrations.ms.base.locking import ms_app_lock
from integrations.ms.base.logger import get_ms_logger
from integrations.ms.base.retry import RetryPolicy, compute_backoff_seconds
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import (
    KIND_APPEND_EXCEL_ROW,
    KIND_INSERT_EXCEL_ROW,
    KIND_UPLOAD_SHAREPOINT_FILE,
)
from integrations.ms.outbox.persistence.repo import MsOutboxRepository

logger = get_ms_logger(__name__)


# Chapter 5 parity with QBO: dead-letter after 5 failed attempts.
MAX_ATTEMPTS = 5

# Cross-process drain lock.
DRAIN_LOCK_NAME = "ms_outbox_drain"
DRAIN_LOCK_TIMEOUT_MS = 1000


class MsOutboxWorker:
    """
    Drain loop for `[ms].[Outbox]`. Called periodically by an APScheduler
    job (task 3.6). Each tick:

      1. Acquires a cross-process drain lock via `ms_app_lock`.
      2. Claims the oldest ready row via `ClaimNextPendingMsOutbox`.
      3. Dispatches by `kind` to the appropriate handler.
      4. Marks the row done / failed / dead_letter.

    Retry: on retryable `MsGraphError` the row is re-scheduled with jittered
    backoff. After `MAX_ATTEMPTS` or any non-retryable error, the row goes to
    `dead_letter`.

    Dead-letter escalation (task 3.8): Excel-bound kinds create a critical
    `MsReconciliationIssue` so the operator sees the failure; silent dead-
    lettering is not acceptable for Excel per the user's explicit requirement.
    """

    def __init__(self, repo: Optional[MsOutboxRepository] = None):
        self.repo = repo or MsOutboxRepository()
        self._dispatch_table: Dict[str, Callable[[MsOutbox, Dict[str, Any]], None]] = {
            KIND_UPLOAD_SHAREPOINT_FILE: self._handle_upload_sharepoint_file,
            KIND_APPEND_EXCEL_ROW: self._handle_append_excel_row,
            KIND_INSERT_EXCEL_ROW: self._handle_insert_excel_row,
        }
        self._retry_policy = RetryPolicy.for_writes()

    # ------------------------------------------------------------------ #
    # Drain loop entry points
    # ------------------------------------------------------------------ #

    def drain_once(self) -> bool:
        """
        Claim and process at most one row. True if a row was processed
        (success or not); False if nothing ready or lock busy.
        """
        with ms_app_lock(DRAIN_LOCK_NAME, timeout_ms=DRAIN_LOCK_TIMEOUT_MS) as got_lock:
            if not got_lock:
                logger.debug("ms.outbox.drain.skipped_lock_busy")
                return False

            row = self.repo.claim_next_pending()
            if not row:
                return False

            self._process(row)
            return True

    def drain_all(self, max_rows: int = 100) -> int:
        """Drain up to `max_rows` in a loop. Returns count processed."""
        processed = 0
        while processed < max_rows:
            if not self.drain_once():
                break
            processed += 1
        return processed

    # ------------------------------------------------------------------ #
    # Per-row processing
    # ------------------------------------------------------------------ #

    def _process(self, row: MsOutbox) -> None:
        if row.correlation_id:
            set_correlation_id(row.correlation_id)
        else:
            ensure_correlation_id()

        logger.info(
            "ms.outbox.row.drained",
            extra={
                "event_name": "ms.outbox.row.drained",
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "tenant_id": row.tenant_id,
                "attempt": (row.attempts or 0) + 1,
            },
        )

        handler = self._dispatch_table.get(row.kind)
        if handler is None:
            self._dead_letter(row, f"Unknown outbox kind: {row.kind}")
            return

        try:
            payload_dict = self._parse_payload(row)
        except ValueError as error:
            self._dead_letter(row, f"Invalid payload JSON: {error}")
            return

        try:
            # Thread the row's stable RequestId into every Graph write the
            # handler makes. On retry, the same key is reused → Graph dedups.
            with idempotency_key_context(row.request_id):
                handler(row, payload_dict)
        except MsGraphError as error:
            self._handle_ms_error(row, error)
            return
        except Exception as error:
            logger.exception(
                "ms.outbox.row.unexpected_error",
                extra={
                    "event_name": "ms.outbox.row.unexpected_error",
                    "outbox_public_id": row.public_id,
                    "error_class": type(error).__name__,
                },
            )
            self._dead_letter(row, f"Unexpected {type(error).__name__}: {error}")
            return

        # Success path
        self.repo.mark_done(id=row.id, row_version=row.row_version)
        logger.info(
            "ms.outbox.row.completed",
            extra={
                "event_name": "ms.outbox.row.completed",
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "tenant_id": row.tenant_id,
                "attempts": (row.attempts or 0) + 1,
                "outcome": "success",
            },
        )

    @staticmethod
    def _parse_payload(row: MsOutbox) -> Dict[str, Any]:
        if not row.payload:
            return {}
        try:
            parsed = json.loads(row.payload)
            if not isinstance(parsed, dict):
                raise ValueError("payload is not a JSON object")
            return parsed
        except json.JSONDecodeError as error:
            raise ValueError(f"payload is not valid JSON: {error}") from error

    def _handle_ms_error(self, row: MsOutbox, error: MsGraphError) -> None:
        attempts_so_far = (row.attempts or 0) + 1
        next_attempt = attempts_so_far + 1

        if not error.is_retryable:
            logger.warning(
                "ms.outbox.row.non_retryable_failure",
                extra={
                    "event_name": "ms.outbox.row.non_retryable_failure",
                    "outbox_public_id": row.public_id,
                    "error_class": type(error).__name__,
                    "ms_error_code": error.code,
                    "http_status": error.http_status,
                },
            )
            self._dead_letter(row, f"{type(error).__name__}: {error}")
            return

        if next_attempt > MAX_ATTEMPTS:
            logger.error(
                "ms.outbox.row.retry_exhausted",
                extra={
                    "event_name": "ms.outbox.row.retry_exhausted",
                    "outbox_public_id": row.public_id,
                    "attempts": attempts_so_far,
                    "max_attempts": MAX_ATTEMPTS,
                    "error_class": type(error).__name__,
                },
            )
            self._dead_letter(row, f"Retries exhausted after {attempts_so_far}: {error}")
            return

        backoff_seconds = compute_backoff_seconds(
            attempt=attempts_so_far,
            policy=self._retry_policy,
            retry_after_seconds=error.retry_after_seconds,
        )
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)

        self.repo.mark_failed(
            id=row.id,
            row_version=row.row_version,
            next_retry_at=next_retry_at,
            last_error=f"{type(error).__name__}: {error}",
        )
        logger.warning(
            "ms.outbox.row.retry_scheduled",
            extra={
                "event_name": "ms.outbox.row.retry_scheduled",
                "outbox_public_id": row.public_id,
                "attempts": attempts_so_far,
                "next_attempt": next_attempt,
                "sleep_seconds": backoff_seconds,
                "next_retry_at": next_retry_at.isoformat(),
                "error_class": type(error).__name__,
                "ms_error_code": error.code,
            },
        )

    def _dead_letter(self, row: MsOutbox, last_error: str) -> None:
        self.repo.mark_dead_letter(
            id=row.id,
            row_version=row.row_version,
            last_error=last_error,
        )
        logger.error(
            "ms.outbox.row.dead_lettered",
            extra={
                "event_name": "ms.outbox.row.dead_lettered",
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "last_error": last_error,
            },
        )

        # Escalation hook (task 3.8). Excel kinds become critical
        # ReconciliationIssue so the operator sees the failure. Other kinds
        # still flag but at lower severity.
        try:
            self._escalate_dead_letter(row, last_error)
        except Exception:
            logger.exception(
                "ms.outbox.dead_letter.escalation_failed",
                extra={
                    "event_name": "ms.outbox.dead_letter.escalation_failed",
                    "outbox_public_id": row.public_id,
                },
            )

    def _escalate_dead_letter(self, row: MsOutbox, last_error: str) -> None:
        """Create a ReconciliationIssue so the dead-letter isn't invisible."""
        # Only escalate for kinds where dropping silently would be harmful.
        if row.kind not in (
            KIND_APPEND_EXCEL_ROW,
            KIND_INSERT_EXCEL_ROW,
            KIND_UPLOAD_SHAREPOINT_FILE,
        ):
            return

        from integrations.ms.reconciliation.business.service import (
            MsReconciliationIssueService,
        )

        payload = self._parse_payload(row) if row.payload else {}
        drive_item_id = payload.get("item_id") or payload.get("drive_item_id")
        worksheet_name = payload.get("worksheet_name")

        MsReconciliationIssueService().flag_dead_letter(
            kind=row.kind,
            entity_type=row.entity_type,
            entity_public_id=row.entity_public_id,
            tenant_id=row.tenant_id,
            outbox_public_id=row.public_id,
            details=last_error,
            drive_item_id=drive_item_id,
            worksheet_name=worksheet_name,
        )

    # ------------------------------------------------------------------ #
    # Per-kind handlers
    # ------------------------------------------------------------------ #

    def _handle_upload_sharepoint_file(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
    ) -> None:
        """
        Upload a file to SharePoint. Supports both simple PUT (≤4MB) and
        upload-session (>4MB) via `upload_small_file` / `upload_large_file`
        in sharepoint/external/client.py.

        Payload shape:
          {
            "drive_id": "...",
            "parent_item_id": "...",
            "filename": "...",
            "content_type": "...",
            "blob_path": "attachments/<public_id>.pdf",  // Azure blob storage path
            # Populated by the large-file path after session creation:
            "upload_session_url": "https://...",
            "completed_bytes": 0,
            "total_bytes": null
          }

        Task 3.5 (resumable upload): for large files, after creating the
        upload session we persist `upload_session_url` + `completed_bytes`
        into the row's Payload. On retry, the handler reads the persisted
        state and resumes from the last completed offset.
        """
        drive_id = payload.get("drive_id")
        parent_item_id = payload.get("parent_item_id")
        filename = payload.get("filename")
        content_type = payload.get("content_type") or "application/octet-stream"
        blob_path = payload.get("blob_path")

        if not all([drive_id, parent_item_id, filename, blob_path]):
            raise ValueError(
                f"upload payload missing required fields: got {list(payload.keys())}"
            )

        content = self._fetch_blob(blob_path)
        total_size = len(content)
        # Note: no worker-side compression. PDFs are already compacted at
        # attachment-upload time via `shared/pdf_utils.compact_pdf`. Images
        # pass through raw. If image compression becomes desired, extend
        # `compact_pdf` upstream rather than re-encoding here.

        # Small-file simple PUT path — no resumability needed.
        SMALL_FILE_THRESHOLD = 4 * 1024 * 1024
        if total_size <= SMALL_FILE_THRESHOLD:
            from integrations.ms.sharepoint.external.client import upload_small_file

            result = upload_small_file(
                drive_id=drive_id,
                parent_item_id=parent_item_id,
                filename=filename,
                content=content,
                content_type=content_type,
            )
            self._raise_if_external_error(row, result)
            uploaded_item = result.get("item") if isinstance(result, dict) else None
            self._link_attachment_if_requested(payload, uploaded_item)
            return

        # Large file: upload-session with checkpointed chunks.
        uploaded_item = self._upload_large_file_with_resume(
            row=row,
            payload=payload,
            content=content,
            total_size=total_size,
            content_type=content_type,
        )
        self._link_attachment_if_requested(payload, uploaded_item)

    def _upload_large_file_with_resume(
        self,
        *,
        row: MsOutbox,
        payload: Dict[str, Any],
        content: bytes,
        total_size: int,
        content_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Chunked upload with per-chunk payload checkpoint. On retry, resumes
        from the last `completed_bytes` recorded in the payload. Returns the
        formatted DriveItem on success (or None if the final chunk didn't
        produce a response body)."""
        import httpx

        from integrations.ms.base.client import MsGraphClient

        drive_id = payload["drive_id"]
        parent_item_id = payload["parent_item_id"]
        filename = payload["filename"]

        upload_url = payload.get("upload_session_url")
        completed_bytes = int(payload.get("completed_bytes") or 0)

        # Step 1: ensure we have an upload session.
        if not upload_url:
            with MsGraphClient() as client:
                session = client.post(
                    f"drives/{drive_id}/items/{parent_item_id}:/{filename}:/createUploadSession",
                    json={
                        "item": {
                            "@microsoft.graph.conflictBehavior": "replace",
                            "name": filename,
                        }
                    },
                    timeout_tier="B",
                    operation_name="driveitem.create_upload_session",
                )
            upload_url = session.get("uploadUrl") if isinstance(session, dict) else None
            if not upload_url:
                raise ValueError("upload session did not return an uploadUrl")
            payload["upload_session_url"] = upload_url
            payload["total_bytes"] = total_size
            updated = self.repo.update_payload(
                id=row.id,
                row_version=row.row_version,
                payload=json.dumps(payload),
            )
            # Keep our in-memory row.row_version in sync so subsequent mark_*
            # calls succeed (ROWVERSION advanced after the payload update).
            if updated and updated.row_version:
                row.row_version = updated.row_version

        # Step 2: chunk upload from `completed_bytes`.
        CHUNK_SIZE = 5 * 1024 * 1024
        offset = completed_bytes
        last_json: Optional[dict] = None

        with httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0)
        ) as http:
            while offset < total_size:
                chunk = content[offset: offset + CHUNK_SIZE]
                chunk_len = len(chunk)
                chunk_resp = http.put(
                    upload_url,
                    headers={
                        "Content-Length": str(chunk_len),
                        "Content-Range": f"bytes {offset}-{offset + chunk_len - 1}/{total_size}",
                        "Content-Type": content_type,
                    },
                    content=chunk,
                )
                if chunk_resp.status_code not in (200, 201, 202):
                    raise RuntimeError(
                        f"Chunk upload failed at offset {offset} "
                        f"(status {chunk_resp.status_code}): {chunk_resp.text[:200]}"
                    )
                if chunk_resp.status_code in (200, 201):
                    try:
                        last_json = chunk_resp.json()
                    except Exception:
                        last_json = None
                offset += chunk_len

                # Checkpoint after every chunk so a retry resumes from here.
                payload["completed_bytes"] = offset
                updated = self.repo.update_payload(
                    id=row.id,
                    row_version=row.row_version,
                    payload=json.dumps(payload),
                )
                if updated and updated.row_version:
                    row.row_version = updated.row_version

        if last_json is None:
            # Final chunk didn't produce a JSON body (rare). Treat as success
            # rather than flag — Graph's contract is that 200/201 on the last
            # chunk returns the DriveItem. Missing means Graph-side config
            # oddity, but the file is uploaded.
            logger.warning(
                "ms.sharepoint.upload.completed_without_response_body",
                extra={
                    "event_name": "ms.sharepoint.upload.completed_without_response_body",
                    "outbox_public_id": row.public_id,
                    "filename": filename,
                },
            )
            return None

        # Format the final item shape consistent with upload_small_file's
        # return for downstream link logic.
        from integrations.ms.sharepoint.external.client import _format_drive_item
        return _format_drive_item(last_json)

    def _handle_append_excel_row(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
    ) -> None:
        """
        Append rows to an Excel worksheet. Payload:
          {
            "drive_id": "...",
            "item_id": "...",
            "worksheet_name": "...",
            "values": [[...]],
            "session_id": null   // optional workbook session
          }
        """
        from integrations.ms.sharepoint.external.client import append_excel_rows

        required = ("drive_id", "item_id", "worksheet_name", "values")
        missing = [k for k in required if payload.get(k) is None]
        if missing:
            raise ValueError(f"append_excel_row payload missing fields: {missing}")

        result = append_excel_rows(
            drive_id=payload["drive_id"],
            item_id=payload["item_id"],
            worksheet_name=payload["worksheet_name"],
            values=payload["values"],
            session_id=payload.get("session_id"),
        )
        self._raise_if_external_error(row, result)

    def _handle_insert_excel_row(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
    ) -> None:
        """
        Insert rows into an Excel worksheet at a specific row_index. Payload:
          {
            "drive_id": "...",
            "item_id": "...",
            "worksheet_name": "...",
            "row_index": 5,
            "values": [[...]],
            "session_id": null
          }
        """
        from integrations.ms.sharepoint.external.client import insert_excel_rows

        required = ("drive_id", "item_id", "worksheet_name", "row_index", "values")
        missing = [k for k in required if payload.get(k) is None]
        if missing:
            raise ValueError(f"insert_excel_row payload missing fields: {missing}")

        result = insert_excel_rows(
            drive_id=payload["drive_id"],
            item_id=payload["item_id"],
            worksheet_name=payload["worksheet_name"],
            row_index=int(payload["row_index"]),
            values=payload["values"],
            session_id=payload.get("session_id"),
        )
        self._raise_if_external_error(row, result)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _raise_if_external_error(row: MsOutbox, result: dict) -> None:
        """
        The sharepoint external client still returns dict envelopes (Option X).
        If the status_code indicates failure, re-raise as a typed MsGraphError
        so the worker's retry/dead-letter logic picks it up.
        """
        status_code = result.get("status_code", 500) if isinstance(result, dict) else 500
        if 200 <= status_code < 300:
            return

        message = result.get("message") if isinstance(result, dict) else "unknown"
        # Map status_code to the right MsGraphError subclass so retry logic
        # picks up the correct is_retryable value.
        from integrations.ms.base.errors import (
            MsAuthError,
            MsClientError,
            MsConflictError,
            MsNotFoundError,
            MsRateLimitError,
            MsServerError,
            MsServiceUnavailableError,
            MsUnexpectedError,
            MsValidationError,
        )

        if status_code == 400:
            raise MsValidationError(message, http_status=status_code)
        if status_code in (401, 403):
            raise MsAuthError(message, http_status=status_code)
        if status_code == 404:
            raise MsNotFoundError(message, http_status=status_code)
        if status_code == 409:
            raise MsConflictError(message, http_status=status_code)
        if status_code == 429:
            raise MsRateLimitError(message, http_status=status_code)
        if status_code == 503:
            raise MsServiceUnavailableError(message, http_status=status_code)
        if 400 <= status_code < 500:
            raise MsClientError(message, http_status=status_code)
        if 500 <= status_code < 600:
            raise MsServerError(message, http_status=status_code)
        raise MsUnexpectedError(message, http_status=status_code)

    @staticmethod
    def _link_attachment_if_requested(
        payload: Dict[str, Any],
        uploaded_item: Optional[Dict[str, Any]],
    ) -> None:
        """
        If the payload specifies `attachment_id`, link the uploaded DriveItem
        back to the Attachment record. Failure is non-fatal — the upload has
        succeeded and we don't want to dead-letter over a linking issue.
        """
        attachment_id = payload.get("attachment_id")
        if not attachment_id or not uploaded_item:
            return
        ms_driveitem_id = uploaded_item.get("item_id") or uploaded_item.get("id")
        if not ms_driveitem_id:
            logger.warning(
                "ms.outbox.upload.link_skipped_no_item_id",
                extra={
                    "event_name": "ms.outbox.upload.link_skipped_no_item_id",
                    "attachment_id": attachment_id,
                },
            )
            return
        try:
            from integrations.ms.sharepoint.driveitem.connector.attachment.business.service import (
                DriveItemAttachmentConnector,
            )

            DriveItemAttachmentConnector().link_driveitem_to_attachment(
                attachment_id=attachment_id,
                ms_driveitem_id=ms_driveitem_id,
            )
        except Exception:
            logger.exception(
                "ms.outbox.upload.link_failed",
                extra={
                    "event_name": "ms.outbox.upload.link_failed",
                    "attachment_id": attachment_id,
                    "ms_driveitem_id": ms_driveitem_id,
                },
            )

    @staticmethod
    def _fetch_blob(blob_path: str) -> bytes:
        """
        Fetch attachment content from Azure Blob Storage. `blob_path` is the
        `blob_url` field from the Attachment entity. Centralized here so
        future changes (streaming, chunked fetch) land in one place.
        """
        from shared.storage import AzureBlobStorage

        content, _metadata = AzureBlobStorage().download_file(blob_path)
        return content
