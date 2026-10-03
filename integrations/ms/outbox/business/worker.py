# Python Standard Library Imports
import base64
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
from integrations.ms.base.errors import MsGraphError, MsNotFoundError, MsServerError
from integrations.ms.base.locking import ms_app_lock
from integrations.ms.base.logger import get_ms_logger
from integrations.ms.base.retry import RetryPolicy, compute_backoff_seconds
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import (
    KIND_APPEND_EXCEL_ROW,
    KIND_EXPENSE_PULL_FANOUT,
    KIND_INSERT_EXCEL_ROW,
    KIND_SEND_MAIL,
    KIND_UPDATE_DRAFT,
    KIND_UPLOAD_SHAREPOINT_FILE,
    UPDATE_DRAFT_OUTCOME_NOT_A_DRAFT,
    UPDATE_DRAFT_OUTCOME_NOT_FOUND,
    UPDATE_DRAFT_OUTCOME_PATCHED,
)
from integrations.ms.outbox.persistence.repo import MsOutboxRepository
from shared.authz.context import system_authz

logger = get_ms_logger(__name__)


# Chapter 5 parity with QBO: dead-letter after 5 failed attempts.
MAX_ATTEMPTS = 5

# `ExpenseService.sync_to_excel_workbook` / `_upload_attachments_to_module_folder`
# report an UNMAPPED project with these messages. They are configuration, not
# failures — the QBO purchase tick logged them and moved on, and so does the
# fan-out handler (a retry cannot map a project).
_UNMAPPED_MESSAGE_PREFIXES = (
    "Excel not linked for project",
    "Module folder not linked for project",
)


def _is_unmapped_result(result: Dict[str, Any]) -> bool:
    message = str((result or {}).get("message") or "")
    return any(message.startswith(prefix) for prefix in _UNMAPPED_MESSAGE_PREFIXES)

# Cross-process drain lock.
DRAIN_LOCK_NAME = "ms_outbox_drain"
DRAIN_LOCK_TIMEOUT_MS = 1000

# Column Z (0-based) of a DETAILS row — the line-item public_id that keys
# reconciliation. Same key Box's `apply_rows_to_details` dedups on
# (`DEFAULT_KEY_COL_INDEX`); kept in sync deliberately (U-440a). Not imported
# from the Box package: `integrations/ms` and `integrations/box` are parallel,
# independently-gated integrations and an MS drain must not import Box code.
RECONCILIATION_KEY_COL_INDEX = 25

_UPDATE_DRAFT_TERMINAL_LOG_EVENTS = {
    UPDATE_DRAFT_OUTCOME_NOT_FOUND: "ms.outbox.update_draft.not_found",
    UPDATE_DRAFT_OUTCOME_NOT_A_DRAFT: "ms.outbox.update_draft.not_a_draft",
}


def _reconciliation_key(row_values: Any) -> str:
    """
    Column-Z key of one worksheet/candidate row, normalized for comparison.

    Returns "" for a short row, a missing cell, or a blank — i.e. "no key",
    which callers must treat as *unprovable*, never as a match. Sheet rows and
    candidate rows both go through here so the two sides can never drift.
    """
    if len(row_values) <= RECONCILIATION_KEY_COL_INDEX:
        return ""
    raw = row_values[RECONCILIATION_KEY_COL_INDEX]
    if raw is None:
        return ""
    return str(raw).strip().lower()


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
            KIND_SEND_MAIL: self._handle_send_mail,
            KIND_UPDATE_DRAFT: self._handle_update_draft,
            KIND_EXPENSE_PULL_FANOUT: self._handle_expense_pull_fanout,
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

    def drain_all(
        self,
        max_rows: int = 100,
        time_budget_seconds: Optional[float] = None,
    ) -> int:
        """
        Drain up to `max_rows` in a loop, stopping early once
        `time_budget_seconds` has elapsed (checked between rows — a row that is
        already in flight always finishes). Returns count processed.

        The scheduler's 30s `drain_ms_outbox` timer used to call `drain_once`,
        i.e. ONE row per tick: a single-attachment expense completion (3-4 rows)
        took ~2 minutes to land and a pull of 20 expenses queued behind
        everything else for the better part of an hour. The Box drain already
        runs a bounded loop (`BoxOutboxWorker.drain_all(max_rows=20,
        time_budget_seconds=20.0)`); this is the same shape.
        """
        processed = 0
        started = datetime.now(timezone.utc)
        while processed < max_rows:
            if (
                time_budget_seconds is not None
                and (datetime.now(timezone.utc) - started).total_seconds() >= time_budget_seconds
            ):
                logger.info(
                    "ms.outbox.drain.time_budget_reached",
                    extra={
                        "event_name": "ms.outbox.drain.time_budget_reached",
                        "processed": processed,
                        "time_budget_seconds": time_budget_seconds,
                    },
                )
                break
            if not self.drain_once():
                break
            processed += 1
        return processed

    # ------------------------------------------------------------------ #
    # Per-row processing
    # ------------------------------------------------------------------ #

    def _process(self, row: MsOutbox) -> None:
        # Drain workers process rows that span all users by design — assert
        # system intent at the boundary via the shared `system_authz()`
        # contextmanager so callers (HTTP endpoint, in-process scheduler,
        # REPL) don't hand-roll save/restore. Prior context is restored on
        # exit so we don't leak system-admin into whatever ran us.
        with system_authz():
            self._process_inner(row)

    def _process_inner(self, row: MsOutbox) -> None:
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
            KIND_SEND_MAIL,
            KIND_EXPENSE_PULL_FANOUT,
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

    def _handle_expense_pull_fanout(self, row: MsOutbox, payload: Dict[str, Any]) -> None:
        """
        Run the Excel + SharePoint fan-out for one (expense, project) that the
        QBO purchase tick projected. Re-reads the expense at drain time (the
        payload carries only ids), then calls the same two `ExpenseService`
        methods the tick used to call inline. Both return result dicts rather
        than raising; a non-empty `errors` list here is turned back into an
        exception so the row retries and eventually dead-letters with a
        ReconciliationIssue instead of vanishing into a WARN.

        An unmapped project (no Excel workbook / no module folder) is a
        configuration state, not a failure: it is logged and the row is done,
        matching what the inline path did (a warning, no retry).
        """
        from entities.expense.business.service import ExpenseService
        from entities.expense_line_item.business.service import ExpenseLineItemService

        project_id = payload.get("project_id")
        if project_id is None:
            raise ValueError("expense_pull_fanout payload missing project_id")
        project_id = int(project_id)

        expense_service = ExpenseService()
        expense = expense_service.read_by_public_id(public_id=str(row.entity_public_id))
        if expense is None:
            # Deleted between enqueue and drain — nothing to file for.
            logger.warning(
                "ms.outbox.expense_pull_fanout.expense_missing",
                extra={
                    "event_name": "ms.outbox.expense_pull_fanout.expense_missing",
                    "outbox_public_id": row.public_id,
                    "entity_public_id": row.entity_public_id,
                },
            )
            return

        line_items = ExpenseLineItemService().read_by_expense_id(expense_id=expense.id) or []
        project_items = [li for li in line_items if li.project_id == project_id]
        if not project_items:
            logger.info(
                "ms.outbox.expense_pull_fanout.no_lines_for_project",
                extra={
                    "event_name": "ms.outbox.expense_pull_fanout.no_lines_for_project",
                    "entity_public_id": row.entity_public_id,
                    "project_id": project_id,
                },
            )
            return
        line_items_count = int(payload.get("expense_line_items_count") or len(line_items))

        errors: list = []

        excel_result = expense_service.sync_to_excel_workbook(
            expense=expense, line_items=project_items, project_id=project_id,
        )
        if _is_unmapped_result(excel_result):
            logger.info(
                "ms.outbox.expense_pull_fanout.excel_unmapped",
                extra={"entity_public_id": row.entity_public_id, "project_id": project_id,
                       "message": excel_result.get("message")},
            )
        else:
            errors.extend(excel_result.get("errors") or [])

        sp_result = expense_service._upload_attachments_to_module_folder(
            expense=expense,
            line_items=project_items,
            project_id=project_id,
            expense_line_items_count=line_items_count,
        )
        if _is_unmapped_result(sp_result):
            logger.info(
                "ms.outbox.expense_pull_fanout.sharepoint_unmapped",
                extra={"entity_public_id": row.entity_public_id, "project_id": project_id,
                       "message": sp_result.get("message")},
            )
        else:
            errors.extend(sp_result.get("errors") or [])

        logger.info(
            "ms.outbox.expense_pull_fanout.completed",
            extra={
                "event_name": "ms.outbox.expense_pull_fanout.completed",
                "entity_public_id": row.entity_public_id,
                "project_id": project_id,
                "excel_rows": excel_result.get("synced_count", 0),
                "sharepoint_uploads": sp_result.get("synced_count", 0),
                "sharepoint_skipped": sp_result.get("skipped_count", 0),
                "error_count": len(errors),
            },
        )
        if errors:
            raise RuntimeError(
                "expense_pull_fanout: " + "; ".join(str(e.get("error", e)) for e in errors)
            )

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
            return

        # Large file: upload-session with checkpointed chunks.
        self._upload_large_file_with_resume(
            row=row,
            payload=payload,
            content=content,
            total_size=total_size,
            content_type=content_type,
        )

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
        from integrations.ms.base.paths import encode_path_segment

        drive_id = payload["drive_id"]
        parent_item_id = payload["parent_item_id"]
        filename = payload["filename"]

        upload_url = payload.get("upload_session_url")
        completed_bytes = int(payload.get("completed_bytes") or 0)

        # Step 1: ensure we have an upload session.
        if not upload_url:
            with MsGraphClient() as client:
                session = client.post(
                    f"drives/{drive_id}/items/{parent_item_id}:/{encode_path_segment(filename)}:/createUploadSession",
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
            self._persist_payload(row, payload)

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
                self._persist_payload(row, payload)

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

    def _filter_rows_already_in_worksheet(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
        kind: str,
    ) -> list:
        """
        Drain-time column-Z idempotency guard for the two row-WRITING kinds (U-440a).

        Why this exists at DRAIN time and not only at enqueue: the writers
        (`{Bill,Expense}Service.sync_to_excel_workbook`,
        `BillCreditCompleteService.sync_to_excel_workbook`) read column Z and
        decide skip-vs-insert when they ENQUEUE. The drain then replayed that
        decision blindly, so anything that re-drove a row — a retry after a
        partial failure, or a future stuck-claim reclaim (U-440b) — inserted a
        SECOND copy of a line that was already in the sheet. That is the
        2026-08-06 incident: 27 duplicate DETAILS rows across 8 client trackers.

        Box has never had this exposure — `apply_rows_to_details` re-reads
        column Z at drain and skips-present. This brings the MS side to the same
        contract, and is the fix `entities/completion_job/business/service.py`
        already names as the proper one for its reclaim-overlap residual.

        Contract:
          - Read fails (non-200, or 200 with no range) -> RAISE. Fail closed.
            A blind insert on an unreadable sheet is exactly how duplicates got
            in; a raised error retries and, after MAX_ATTEMPTS, dead-letters
            VISIBLY.
          - A row whose column-Z key is already present -> dropped.
          - A row with a blank/missing column-Z key -> KEPT. It cannot be proven
            a duplicate, and silently dropping it would lose real ledger data —
            strictly worse than the duplicate it might create.

        Returns the rows that still need writing (possibly empty).
        """
        from integrations.ms.sharepoint.external.client import (
            get_excel_used_range_values,
        )

        values = payload["values"]
        result = get_excel_used_range_values(
            payload["drive_id"],
            payload["item_id"],
            payload["worksheet_name"],
            session_id=payload.get("session_id"),
        )
        # Fail closed on a bad status...
        self._raise_if_external_error(row, result)
        # ...and on a 200 that carries no range: _raise_if_external_error only
        # inspects status_code, and a rangeless 200 would otherwise read as
        # "no existing keys" and let every row through as if the sheet were empty.
        worksheet = result.get("range") if isinstance(result, dict) else None
        if not worksheet:
            # MsServerError, not the base MsGraphError: base `is_retryable` is
            # False, and `_handle_ms_error` dead-letters a non-retryable on
            # attempt 1 — which would permanently strand a legitimate ledger row
            # on one transient rangeless read. A response-shape anomaly is
            # server-side and retryable; after MAX_ATTEMPTS it still dead-letters
            # VISIBLY, which is the contract this guard documents (U-440a round 2).
            raise MsServerError(
                f"{kind}: used-range read returned no range for worksheet "
                f"{payload['worksheet_name']!r}; refusing to write blind"
            )

        # Both sides of the comparison MUST normalize identically — a sheet-side
        # and candidate-side extraction that drift apart stop matching and the
        # dedup silently stops working. One helper, used for both (Pass 2).
        existing = {
            key
            for key in (_reconciliation_key(r) for r in (worksheet.get("values") or []))
            if key
        }

        keep = []
        skipped = 0
        for candidate in values:
            key = _reconciliation_key(candidate)
            if key and key in existing:
                skipped += 1
                continue
            if key:
                # Retained keys join the seen-set so a batch that repeats a key
                # writes it ONCE. Parity with Box's `apply_rows_to_details`,
                # which adds to `existing_keys` as it applies (U-440a round 2).
                existing.add(key)
            keep.append(candidate)

        if skipped:
            logger.info(
                "ms.outbox.excel.drain_dedup_skipped",
                extra={
                    "event_name": "ms.outbox.excel.drain_dedup_skipped",
                    "outbox_id": row.id,
                    "kind": kind,
                    "skipped": skipped,
                    "remaining": len(keep),
                },
            )
        return keep

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

        values = self._filter_rows_already_in_worksheet(
            row, payload, KIND_APPEND_EXCEL_ROW
        )
        if not values:
            return

        result = append_excel_rows(
            drive_id=payload["drive_id"],
            item_id=payload["item_id"],
            worksheet_name=payload["worksheet_name"],
            values=values,
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

        # U-440a: drop rows already carrying their column-Z key in the sheet.
        # `row_index` stays as enqueued — it is the SubCostCode-section insertion
        # point, and writing fewer rows at that same point is still correct.
        values = self._filter_rows_already_in_worksheet(
            row, payload, KIND_INSERT_EXCEL_ROW
        )
        if not values:
            return

        result = insert_excel_rows(
            drive_id=payload["drive_id"],
            item_id=payload["item_id"],
            worksheet_name=payload["worksheet_name"],
            row_index=int(payload["row_index"]),
            values=values,
            session_id=payload.get("session_id"),
        )
        self._raise_if_external_error(row, result)

    def _handle_send_mail(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
    ) -> None:
        """
        Send an email or create a draft, depending on payload.mode.

        Payload shape:
          {
            "to_addresses":  [{"address": "...", "name": "..."}, ...],
            "cc_addresses":  [...],
            "bcc_addresses": [...],
            "subject":       "...",
            "body":          "<p>...</p>",
            "body_type":     "HTML" | "Text",
            "attachment":    legacy {name, content_type, content_bytes} |
                             reference {name, content_type, blob_url} |
                             None,
            "mode":          "draft" | "send",
            "review_id":     <int>,
            "bill_id":       <int>
          }

        Attachment shapes are detected by key presence (``"content_bytes"
        in attachment``, including the empty string ``b64encode(b"")``
        produces — not truthiness, and not a version flag). The legacy
        branch is cheap insurance for the one cancelled send_mail row that
        still carries embedded base64. It is not covering in-flight
        pending/in_progress rows: prod has none, and this worker ships in
        the same image as enqueue (the scheduler drain is a timer POSTing
        ``/admin/outbox/drain/ms``).

        A reference fetch either succeeds or the row retries / dead-letters
        — this handler does not call Graph with an unresolved reference.
        That is not the same as "the reviewer always gets a PDF":
        ``create_draft`` currently logs and swallows a non-201 from
        ``add_attachment_to_message`` and still returns 201 (pre-existing,
        ``integrations/ms/mail/external/client.py``). A 404 blob miss
        dead-letters on attempt 1; other fetch failures retry across a
        2–3 minute window (see ``_resolve_send_mail_attachment``).

        On success the worker stamps the resulting Graph message_id (for
        drafts) back into the row's payload for audit traceability, plus
        ``conversation_id`` / ``internet_message_id`` when Graph returned
        them (U-579 — the only moment those are knowable; see below). The
        fetched bytes stay in a local; they are not written back onto
        ``payload["attachment"]``.
        """
        from integrations.ms.mail.external.client import (
            create_draft,
            create_forward_draft,
            forward_message,
            send_draft,
            send_message,
        )

        to_addresses = payload.get("to_addresses") or []
        cc_addresses = payload.get("cc_addresses") or []
        bcc_addresses = payload.get("bcc_addresses") or []
        subject = payload.get("subject") or ""
        body = payload.get("body") or ""
        body_type = payload.get("body_type") or "HTML"
        attachment = payload.get("attachment")
        mode = (payload.get("mode") or "draft").lower()
        forward_message_id = payload.get("forward_message_id")
        comment_text = payload.get("comment_text") or ""
        html_preamble = payload.get("html_preamble") or None

        if not to_addresses and not cc_addresses and not bcc_addresses:
            # Drafts may legitimately have no recipients yet — e.g. the
            # ContractLabor per-project review draft for a project with
            # no PM configured. The user manually addresses + sends from
            # Outlook Drafts. `send` mode still requires recipients.
            if mode != "draft":
                raise ValueError("send_mail payload has no recipients on any line")

        # Forward inherits the source message's attachments; a reference
        # fetch here would be unused work. Resolve only for new-mail.
        # Bind a local — do not assign onto payload["attachment"], or the
        # success-path update_payload would persist fetched base64.
        if attachment and not forward_message_id:
            attachment = self._resolve_send_mail_attachment(attachment)
        attachments = [attachment] if attachment else None

        if forward_message_id:
            # Forward path — inherit subject/body/attachments from the
            # source message; `comment_text` becomes the plain-text
            # preamble. `subject` / `body` / `attachment` ignored.
            if mode == "send" and html_preamble:
                # Graph's /forward endpoint only accepts a plain-text
                # `comment` (it strips newlines + ignores HTML). To send
                # an immediate forward with a formatted HTML preamble
                # (yellow callout boxes, line breaks, styled labels) we
                # have to draft-then-send: createForward + PATCH body
                # gives us the HTML injection, then /send dispatches.
                draft_result = create_forward_draft(
                    message_id=forward_message_id,
                    html_preamble=html_preamble,
                    to_recipients=to_addresses or None,
                    cc_recipients=cc_addresses or None,
                    bcc_recipients=bcc_addresses or None,
                )
                draft_id = (
                    ((draft_result or {}).get("draft") or {}).get("message_id")
                    if isinstance(draft_result, dict)
                    else None
                )
                if not draft_id:
                    result = draft_result
                else:
                    result = send_draft(draft_id)
            elif mode == "send":
                result = forward_message(
                    message_id=forward_message_id,
                    to_recipients=to_addresses,
                    comment=comment_text or None,
                    cc_recipients=cc_addresses or None,
                    bcc_recipients=bcc_addresses or None,
                )
            elif mode == "draft":
                result = create_forward_draft(
                    message_id=forward_message_id,
                    # html_preamble takes precedence; comment_text is the
                    # fallback for callers that don't need rich formatting.
                    comment=(comment_text or None) if not html_preamble else None,
                    html_preamble=html_preamble,
                    to_recipients=to_addresses or None,
                    cc_recipients=cc_addresses or None,
                    bcc_recipients=bcc_addresses or None,
                )
            else:
                raise ValueError(f"send_mail payload has unknown mode: {mode!r}")
        elif mode == "send":
            result = send_message(
                to_recipients=to_addresses,
                subject=subject,
                body=body,
                body_type=body_type,
                cc_recipients=cc_addresses or None,
                bcc_recipients=bcc_addresses or None,
                attachments=attachments,
            )
        elif mode == "draft":
            result = create_draft(
                to_recipients=to_addresses,
                subject=subject,
                body=body,
                body_type=body_type,
                cc_recipients=cc_addresses or None,
                bcc_recipients=bcc_addresses or None,
                attachments=attachments,
            )
        else:
            raise ValueError(f"send_mail payload has unknown mode: {mode!r}")

        self._raise_if_external_error(row, result)

        # Stamp the returned message id (drafts only — sendMail returns 202
        # with no body) into the payload for audit traceability. The mail
        # client formats the draft via _format_message which exposes the
        # Graph id under "message_id".
        draft = result.get("draft") if isinstance(result, dict) else None
        message_id = (draft or {}).get("message_id") if isinstance(draft, dict) else None
        if message_id:
            payload["graph_message_id"] = message_id
            # U-579: also stamp the CONVERSATION identity the same response
            # already carries (`_format_message` projects conversation_id +
            # internet_message_id alongside message_id). A Graph message id
            # changes when the message moves folders, so a later read-back of
            # `graph_message_id` on a draft the owner has already sent or
            # deleted returns "not found" and the conversation id is lost
            # forever — measured 2026-09-29: 6 drafts cleared the same
            # afternoon, 0 conversation ids recovered by the weekly sweep.
            # Capturing here is the only window that exists.
            #
            # Strictly additive, and only when Graph returned a value:
            # consumers must treat both keys as optional (rows enqueued
            # before this change carry neither).
            # Only a non-empty STRING is a usable identity. Graph returning an
            # unexpected shape must not be persisted as one: a consumer that
            # str()-ed it would get something id-shaped and skip its own fallback.
            conversation_id = draft.get("conversation_id")
            if isinstance(conversation_id, str) and conversation_id.strip():
                payload["conversation_id"] = conversation_id.strip()
            internet_message_id = draft.get("internet_message_id")
            if isinstance(internet_message_id, str) and internet_message_id.strip():
                payload["internet_message_id"] = internet_message_id.strip()
            try:
                self._persist_payload(row, payload)
            except Exception:
                logger.exception(
                    "ms.outbox.send_mail.payload_update_failed",
                    extra={
                        "event_name": "ms.outbox.send_mail.payload_update_failed",
                        "outbox_public_id": row.public_id,
                        "graph_message_id": message_id,
                    },
                )

    def _handle_update_draft(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
    ) -> None:
        """
        PATCH an existing draft in place via Graph `me/messages/{id}`.

        Payload shape:
          {
            "graph_message_id": "<Graph message id>",
            "to_addresses":  [{"email": "...", "name": "..."}, ...],
            "cc_addresses":  [...],
            "bcc_addresses": [...],
            "subject":       "...",
            "body":          "<p>...</p>",
            "body_type":     "HTML" | "Text",
          }

        Stamps factual `update_draft_outcome` values on the row payload (see
        service module constants). None of those outcomes mean the message was sent.

        Read-before-write: GET the message and require `is_draft is True` before
        PATCHing so we do not rewrite a message already known not to be a draft.
        """
        from integrations.ms.mail.external.client import get_message, update_draft

        graph_message_id = payload.get("graph_message_id")
        if not graph_message_id:
            raise ValueError("update_draft payload missing graph_message_id")

        to_addresses = payload.get("to_addresses") or []
        cc_addresses = payload.get("cc_addresses") or []
        bcc_addresses = payload.get("bcc_addresses") or []
        subject = payload.get("subject")
        body = payload.get("body")
        body_type = payload.get("body_type") or "HTML"

        get_result = get_message(message_id=graph_message_id, include_body=False)
        get_terminal = self._classify_update_draft_get_terminal(get_result)
        if get_terminal is not None:
            self._log_update_draft_terminal(
                row,
                payload,
                get_terminal,
                graph_message_id,
                get_result.get("status_code"),
                phase="get",
            )
            return

        if self._is_update_draft_get_inconclusive(get_result):
            raise MsServerError(
                "update_draft GET did not positively prove an updatable draft",
                http_status=503,
            )

        self._raise_if_external_error(row, get_result)

        # Residual race (accepted): a human can send between this GET and PATCH.
        # Graph permits PATCH on a sent message until 2026-12-31, so the consequence
        # is a rewritten sent message. If-Match was removed — unsupported on this
        # contract and previously manufactured false terminal outcomes.
        result = update_draft(
            message_id=graph_message_id,
            to_recipients=to_addresses,
            subject=subject,
            body=body,
            body_type=body_type,
            cc_recipients=cc_addresses,
            bcc_recipients=bcc_addresses,
        )

        if self._is_update_draft_patch_not_found(result):
            self._log_update_draft_terminal(
                row,
                payload,
                UPDATE_DRAFT_OUTCOME_NOT_FOUND,
                graph_message_id,
                result.get("status_code"),
                phase="patch",
            )
            return

        self._raise_unless_update_draft_patch_success(result)

        draft = result.get("draft") if isinstance(result, dict) else None
        message_id = (draft or {}).get("message_id") if isinstance(draft, dict) else None
        if message_id:
            payload["graph_message_id"] = message_id
        self._stamp_update_draft_outcome(row, payload, UPDATE_DRAFT_OUTCOME_PATCHED)

    @staticmethod
    def _classify_update_draft_get_terminal(result: Any) -> Optional[str]:
        """
        Terminal GET facts only. Returns an outcome constant or None (retry/other).
        GET 404 → id does not resolve; 200 + is_draft is False → not a draft.

        Call order vs ``_is_update_draft_get_inconclusive`` is load-bearing: this
        runs first; inconclusive's ``status != 200 -> False`` lets 5xx/401/403 reach
        ``_raise_if_external_error`` instead of being misclassified as inconclusive.
        """
        if not isinstance(result, dict):
            return None
        status_code = result.get("status_code", 500)
        if status_code == 404:
            return UPDATE_DRAFT_OUTCOME_NOT_FOUND
        if status_code != 200:
            return None
        email = result.get("email")
        if not isinstance(email, dict):
            return None
        if email.get("is_draft") is False:
            return UPDATE_DRAFT_OUTCOME_NOT_A_DRAFT
        return None

    @staticmethod
    def _is_update_draft_get_inconclusive(result: Any) -> bool:
        """
        True when GET succeeded numerically but did not prove `is_draft is True`
        (missing email, malformed shape, absent/None is_draft). Caller must retry.
        """
        if not isinstance(result, dict):
            return True
        if result.get("status_code") != 200:
            return False
        email = result.get("email")
        if not isinstance(email, dict):
            return True
        return email.get("is_draft") is not True

    @staticmethod
    def _is_update_draft_patch_not_found(result: Any) -> bool:
        """True when PATCH reports 404 — the stored Graph id does not resolve."""
        if not isinstance(result, dict):
            return False
        return result.get("status_code") == 404

    @staticmethod
    def _raise_unless_update_draft_patch_success(result: Any) -> None:
        """
        PATCH path: only 404 is a factual terminal (handled before this call).
        Any other non-2xx is UNKNOWN — retry through the attempt budget.
        """
        if not isinstance(result, dict):
            raise MsServerError(
                "update_draft PATCH returned non-dict envelope",
                http_status=503,
            )
        status_code = result.get("status_code", 500)
        if 200 <= status_code < 300:
            return
        message = result.get("message") or "update_draft PATCH did not succeed"
        raise MsServerError(message, http_status=503)

    def _persist_payload(self, row: MsOutbox, payload: Dict[str, Any]) -> bool:
        updated = self.repo.update_payload(
            id=row.id,
            row_version=row.row_version,
            payload=json.dumps(payload),
        )
        # Keep our in-memory row.row_version in sync so subsequent mark_*
        # calls succeed (ROWVERSION advanced after the payload update).
        if updated and updated.row_version:
            row.row_version = updated.row_version
            return True
        return False

    def _log_update_draft_terminal(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
        outcome: str,
        graph_message_id: str,
        http_status: Any,
        *,
        phase: str,
    ) -> None:
        self._stamp_update_draft_outcome(row, payload, outcome)
        event_name = _UPDATE_DRAFT_TERMINAL_LOG_EVENTS[outcome]
        logger.info(
            event_name,
            extra={
                "event_name": event_name,
                "outbox_public_id": row.public_id,
                "graph_message_id": graph_message_id,
                "update_draft_outcome": outcome,
                "http_status": http_status,
                "phase": phase,
            },
        )

    def _stamp_update_draft_outcome(
        self,
        row: MsOutbox,
        payload: Dict[str, Any],
        outcome: str,
    ) -> None:
        """Persist drain outcome before the row may be marked done; retry on failure."""
        payload["update_draft_outcome"] = outcome
        try:
            if not self._persist_payload(row, payload):
                raise MsServerError(
                    "update_draft outcome not persisted (row version conflict or no OUTPUT)",
                    http_status=500,
                )
        except Exception as error:
            logger.exception(
                "ms.outbox.update_draft.payload_update_failed",
                extra={
                    "event_name": "ms.outbox.update_draft.payload_update_failed",
                    "outbox_public_id": row.public_id,
                    "graph_message_id": payload.get("graph_message_id"),
                    "update_draft_outcome": outcome,
                },
            )
            raise MsServerError(
                f"Failed to persist update_draft outcome {outcome!r}",
                http_status=500,
            ) from error

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _raise_if_external_error(row: MsOutbox, result: dict) -> None:
        """
        The sharepoint external client still returns dict envelopes (Option X).
        If the status_code indicates failure, re-raise as a typed MsGraphError
        so the worker's retry/dead-letter logic picks it up.

        `is_retryable` (U-224 fix-round 1) disambiguates the 401/403 bucket, where the numeric
        status_code alone can't tell a transient auth failure from a permanent one
        (MsAuthTransientError vs MsAuthError both reconstruct to the same code).

        `is_auth_error` (U-224 fix-round 2) additionally gates the 500-599 fallback bucket. A
        permanent auth failure raised before any HTTP request left the process has no real HTTP
        status and collapses to this fallback with is_retryable=False — but so do unrelated
        exceptions that share that same shape (e.g. MsWriteRefusedError from the ALLOW_MS_WRITES
        gate, or a bare MsGraphError raised for a Graph response-shape anomaly unrelated to auth).
        `is_retryable is False` alone is NOT a safe signal for "this was an auth failure" — only
        reclassify to MsAuthError here when `is_auth_error` is explicitly True, confirming the
        origin exception really was MsAuthError/MsAuthTransientError. Any envelope that doesn't set
        these keys (every hand-built dict in the two external clients that isn't produced by
        _error_response) reads as None here and falls through to the pre-existing
        status_code-only behavior, unchanged.
        """
        status_code = result.get("status_code", 500) if isinstance(result, dict) else 500
        if 200 <= status_code < 300:
            return

        message = result.get("message") if isinstance(result, dict) else "unknown"
        is_retryable_hint = result.get("is_retryable") if isinstance(result, dict) else None
        is_auth_error_hint = result.get("is_auth_error") if isinstance(result, dict) else None
        # Map status_code to the right MsGraphError subclass so retry logic
        # picks up the correct is_retryable value.
        from integrations.ms.base.errors import (
            MsAuthError,
            MsAuthTransientError,
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
            if is_retryable_hint:
                raise MsAuthTransientError(message, http_status=status_code)
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
            if is_auth_error_hint is True and is_retryable_hint is False:
                # Only reached by a confirmed permanent auth failure (is_auth_error verified the
                # origin exception was actually MsAuthError-family, not just any non-retryable
                # exception that happened to collapse to this bucket).
                raise MsAuthError(message, http_status=status_code)
            raise MsServerError(message, http_status=status_code)
        raise MsUnexpectedError(message, http_status=status_code)

    @staticmethod
    def _is_permanent_blob_miss(exc: BaseException) -> bool:
        """True iff this fetch failure means the blob is gone (HTTP 404)."""
        seen: set[int] = set()
        current: Optional[BaseException] = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if getattr(current, "http_status", None) == 404:
                return True
            response = getattr(current, "response", None)
            if response is not None and getattr(response, "status_code", None) == 404:
                return True
            # AzureBlobStorage.download_file wraps HTTPStatusError as
            # AzureBlobStorageError("Failed to download blob: 404").
            if type(current).__name__ == "AzureBlobStorageError":
                if str(current).rstrip().endswith("404"):
                    return True
            current = current.__cause__ or current.__context__
        return False

    def _resolve_send_mail_attachment(
        self, attachment: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Turn a send_mail payload attachment into Graph's
        ``{name, content_type, content_bytes}`` (content_bytes already base64).

        Detected by key presence, not truthiness: ``"content_bytes" in
        attachment`` (including ``""`` from ``b64encode(b"")``) takes the
        legacy path. The branch is cheap insurance for the cancelled
        send_mail row that still carries embedded base64 — worker and
        enqueue ship in the same image, and prod has no pending/
        in_progress send_mail rows.

          * legacy: ``content_bytes`` key present → return as-is; do not re-fetch.
          * reference: ``blob_url`` present → ``_fetch_blob`` then base64.
          * neither: ``ValueError``. ``_process_inner`` treats that as
            unexpected and dead-letters on attempt 1 (no retry). Distinct
            from the fetch-failure paths below.

        A 404 / not-found from ``_fetch_blob`` is permanent (blob deleted):
        raise non-retryable ``MsNotFoundError`` so the row dead-letters
        and escalates on attempt 1. Any other fetch failure raises
        retryable ``MsServerError`` (caught by ``_process_inner`` →
        ``_handle_ms_error``). Backoff is ``RetryPolicy.for_writes()``
        (1.0s base, ×2, full jitter → ~15s of sleep) across
        ``MAX_ATTEMPTS`` (5). ``drain_once()`` processes one row per 30s
        tick, so the total window is **2–3 minutes** before dead-letter.
        A later tick can deliver a transient 503; it will not un-delete
        a 404, and it will not outlast an Azure Storage incident that
        covers the whole window.
        """
        if "content_bytes" in attachment:
            return attachment
        blob_url = attachment.get("blob_url")
        if not blob_url:
            raise ValueError(
                "send_mail attachment has neither content_bytes nor blob_url"
            )
        try:
            content = self._fetch_blob(blob_url)
        except Exception as exc:
            if self._is_permanent_blob_miss(exc):
                logger.warning(
                    "ms.outbox.send_mail.attachment_blob_not_found",
                    extra={
                        "event_name": "ms.outbox.send_mail.attachment_blob_not_found",
                        "blob_url": blob_url,
                        "error_class": type(exc).__name__,
                    },
                )
                raise MsNotFoundError(
                    f"send_mail attachment blob not found: {exc}",
                    http_status=404,
                ) from exc
            logger.warning(
                "ms.outbox.send_mail.attachment_fetch_failed",
                extra={
                    "event_name": "ms.outbox.send_mail.attachment_fetch_failed",
                    "blob_url": blob_url,
                    "error_class": type(exc).__name__,
                },
            )
            raise MsServerError(
                f"send_mail attachment fetch failed: {exc}",
                http_status=503,
            ) from exc
        return {
            "name": attachment.get("name") or "attachment.pdf",
            "content_type": attachment.get("content_type") or "application/pdf",
            "content_bytes": base64.b64encode(content).decode("ascii"),
        }

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
