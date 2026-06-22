# Python Standard Library Imports
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

# Local Imports
from integrations.box.auth.business.service import BoxAuthService
from integrations.box.base.correlation import (
    ensure_correlation_id,
    idempotency_key_context,
    set_correlation_id,
)
from integrations.box.base.errors import (
    BoxError,
    BoxNotFoundError,
    BoxPermissionError,
)
from integrations.box.base.locking import box_app_lock
from integrations.box.base.logger import get_box_logger
from integrations.box.base.retry import RetryPolicy, compute_backoff_seconds
from integrations.box.outbox.business.model import BoxOutbox
from integrations.box.outbox.business.service import KIND_UPDATE_EXCEL, KIND_UPLOAD_FILE
from integrations.box.outbox.persistence.repo import BoxOutboxRepository
from shared.authz.context import (
    current_user_id,
    current_company_id,
    current_is_system_admin,
    set_authz_context,
)

logger = get_box_logger(__name__)


# Cross-process drain lock.
DRAIN_LOCK_NAME = "box_outbox_drain"
DRAIN_LOCK_TIMEOUT_MS = 1000

# Visibility-lost circuit: abort the pass after this many consecutive
# not-found / permission outcomes — the service account has likely lost
# collaboration on the target folders, and burning through the queue would
# dead-letter every row for the same systemic cause.
VISIBILITY_CIRCUIT_THRESHOLD = 3


def _drain_paused() -> bool:
    """
    Operational pause lever (PAUSE_BOX_DRAIN). Checked inside the worker so
    every drain entry point — scheduler tick, admin endpoint, scripts —
    honors it with a single guard. Accepts the same value set as the other
    PAUSE_* flags in shared/api/admin.py.
    """
    return os.getenv("PAUSE_BOX_DRAIN", "").strip().lower() in ("true", "1", "yes")


class BoxOutboxWorker:
    """
    Drain loop for `[box].[Outbox]`. Called periodically by the
    `build.one.scheduler` Function App via the admin drain endpoint. Each
    tick:

      1. Acquires a cross-process drain lock via `box_app_lock`.
      2. Claims the oldest ready row via `ClaimNextPendingBoxOutbox`.
      3. Dispatches by `kind` to the appropriate handler.
      4. Marks the row done / failed / dead_letter.

    Retry: on retryable `BoxError` the row is re-scheduled with jittered
    backoff (same math as MsOutboxWorker). After `MAX_ATTEMPTS` or any
    non-retryable error, the row goes to `dead_letter` and escalates to a
    `BoxReconciliationIssue` (severity critical for upload kinds) so the
    failure isn't invisible.

    Two pass-level circuit breakers in `drain_all`:
      - auth: if a CCG token can't be minted before the pass starts, skip
        the whole pass rather than dead-lettering rows against a down
        token endpoint.
      - visibility: 3 consecutive BoxNotFoundError / BoxPermissionError
        outcomes abort the pass — the service account has probably lost
        collaboration on the mapped folders.
    """

    MAX_ATTEMPTS = 5

    def __init__(self, repo: Optional[BoxOutboxRepository] = None):
        self.repo = repo or BoxOutboxRepository()
        self._dispatch_table: Dict[str, Callable[[BoxOutbox, Dict[str, Any]], None]] = {
            KIND_UPLOAD_FILE: self._handle_upload_box_file,
            # Phase 3 (Excel-in-Box): DETAILS-tab updates to Box-hosted .xlsx
            # workbooks. The handler downloads + edits with openpyxl + uploads
            # a new version (Box has no cell-level API).
            KIND_UPDATE_EXCEL: self._handle_update_box_excel,
        }
        self._retry_policy = RetryPolicy.for_writes()
        # Set by _process_inner after each row; read by drain_all to drive
        # the visibility-lost circuit breaker.
        self._last_outcome_visibility_lost = False

    # ------------------------------------------------------------------ #
    # Drain loop entry points
    # ------------------------------------------------------------------ #

    def drain_once(self) -> Dict[str, int]:
        """
        Claim and process at most one row.

        Returns `{"claimed": 0|1, "done": int, "failed": int,
        "dead_lettered": int}` — all zeros when nothing was ready, the
        drain lock was busy, or PAUSE_BOX_DRAIN is set (the guard lives
        here so BOTH the scheduler tick and the admin endpoint honor the
        pause with one check — rows stay pending, never failed).
        """
        result = {"claimed": 0, "done": 0, "failed": 0, "dead_lettered": 0}
        if _drain_paused():
            logger.debug("box.outbox.drain.paused")
            return result
        self._last_outcome_visibility_lost = False

        with box_app_lock(DRAIN_LOCK_NAME, timeout_ms=DRAIN_LOCK_TIMEOUT_MS) as got_lock:
            if not got_lock:
                logger.debug("box.outbox.drain.skipped_lock_busy")
                return result

            row = self.repo.claim_next_pending()
            if not row:
                return result

            result["claimed"] = 1
            outcome = self._process(row)
            if outcome in result:
                result[outcome] += 1
            return result

    def drain_all(
        self,
        max_rows: int = 20,
        time_budget_seconds: float = 20.0,
    ) -> Dict[str, Any]:
        """
        Drain up to `max_rows` within `time_budget_seconds`.

        Pre-pass auth circuit breaker: mint a CCG token before claiming
        anything. If the token endpoint is unavailable (retryable BoxError),
        skip the entire pass — every row would otherwise burn an attempt
        against an outage that backoff at the row level can't see coming.
        Returns `{"skipped": "auth_unavailable", ...zero counts}` in that
        case.

        Visibility circuit: after 3 consecutive rows whose handler raised
        BoxNotFoundError / BoxPermissionError, abort the pass
        (`box.outbox.visibility_circuit_open`) — the service account has
        likely lost collaboration on the mapped folders and continuing
        would dead-letter the whole queue for one systemic cause.
        """
        totals: Dict[str, Any] = {"claimed": 0, "done": 0, "failed": 0, "dead_lettered": 0}

        if _drain_paused():
            logger.info("box.outbox.drain.paused")
            return {"paused": True, **totals}

        # Box not provisioned in this environment (no CCG creds): bail before
        # touching the network or DB. Keeps the scheduler's box-drain timer a
        # true no-op until a real tenant is configured — no empty-cred token
        # mints to Box every tick.
        auth = BoxAuthService()
        if not auth.is_configured():
            return {"skipped": "box_not_configured", **totals}

        try:
            auth.ensure_valid_token()
        except BoxError as error:
            if error.is_retryable:
                logger.warning(
                    "box.outbox.drain.skipped_auth_unavailable",
                    extra={
                        "event_name": "box.outbox.drain.skipped_auth_unavailable",
                        "error_class": type(error).__name__,
                        "http_status": error.http_status,
                    },
                )
            else:
                # Config/credential problem — also nothing drainable, but
                # worth a louder log since waiting won't fix it.
                logger.error(
                    "box.outbox.drain.skipped_auth_unavailable",
                    extra={
                        "event_name": "box.outbox.drain.skipped_auth_unavailable",
                        "error_class": type(error).__name__,
                        "http_status": error.http_status,
                        "reason": "non_retryable_auth_failure",
                    },
                )
            return {"skipped": "auth_unavailable", **totals}

        start_time = time.monotonic()
        consecutive_visibility_failures = 0

        while totals["claimed"] < max_rows:
            if (time.monotonic() - start_time) >= time_budget_seconds:
                logger.info(
                    "box.outbox.drain.budget_exhausted",
                    extra={
                        "event_name": "box.outbox.drain.budget_exhausted",
                        "claimed": totals["claimed"],
                        "time_budget_seconds": time_budget_seconds,
                    },
                )
                break

            result = self.drain_once()
            if not result["claimed"]:
                break

            for key in ("claimed", "done", "failed", "dead_lettered"):
                totals[key] += result.get(key, 0)

            if self._last_outcome_visibility_lost:
                consecutive_visibility_failures += 1
                if consecutive_visibility_failures >= VISIBILITY_CIRCUIT_THRESHOLD:
                    logger.error(
                        "box.outbox.visibility_circuit_open",
                        extra={
                            "event_name": "box.outbox.visibility_circuit_open",
                            "consecutive_failures": consecutive_visibility_failures,
                            "claimed": totals["claimed"],
                        },
                    )
                    break
            else:
                consecutive_visibility_failures = 0

        return totals

    # ------------------------------------------------------------------ #
    # Per-row processing
    # ------------------------------------------------------------------ #

    def _process(self, row: BoxOutbox) -> str:
        # Drain workers process rows that span all users by design — assert
        # system intent at the boundary so callers (HTTP endpoint, in-process
        # scheduler, REPL) don't have to remember `set_authz_context`. Prior
        # context is restored on exit so we don't leak system-admin into
        # whatever ran us.
        prior_uid = current_user_id.get()
        prior_cid = current_company_id.get()
        prior_isa = current_is_system_admin.get()
        set_authz_context(user_id=None, company_id=None, is_system_admin=True)
        try:
            return self._process_inner(row)
        finally:
            set_authz_context(user_id=prior_uid, company_id=prior_cid, is_system_admin=prior_isa)

    def _process_inner(self, row: BoxOutbox) -> str:
        """Process one claimed row. Returns 'done' | 'failed' | 'dead_lettered'."""
        if row.correlation_id:
            set_correlation_id(row.correlation_id)
        else:
            ensure_correlation_id()

        logger.info(
            "box.outbox.row.drained",
            extra={
                "event_name": "box.outbox.row.drained",
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                # ClaimNextPendingBoxOutbox already incremented Attempts —
                # row.attempts IS the current attempt number (unlike ms,
                # which increments at fail time).
                "attempt": row.attempts or 1,
            },
        )

        handler = self._dispatch_table.get(row.kind)
        if handler is None:
            self._dead_letter(row, f"Unknown outbox kind: {row.kind}")
            return "dead_lettered"

        try:
            payload_dict = self._parse_payload(row)
        except ValueError as error:
            self._dead_letter(row, f"Invalid payload JSON: {error}")
            return "dead_lettered"

        try:
            # Thread the row's stable RequestId through the handler. Box has
            # no client-request-id dedup header — the key is used for log
            # correlation and handler-level dedup (registry guard) only.
            with idempotency_key_context(row.request_id):
                handler(row, payload_dict)
        except BoxError as error:
            if isinstance(error, (BoxNotFoundError, BoxPermissionError)):
                self._last_outcome_visibility_lost = True
            return self._handle_box_error(row, error)
        except Exception as error:
            logger.exception(
                "box.outbox.row.unexpected_error",
                extra={
                    "event_name": "box.outbox.row.unexpected_error",
                    "outbox_public_id": row.public_id,
                    "error_class": type(error).__name__,
                },
            )
            self._dead_letter(row, f"Unexpected {type(error).__name__}: {error}")
            return "dead_lettered"

        # Success path
        self.repo.mark_done(id=row.id, row_version=row.row_version)
        logger.info(
            "box.outbox.row.completed",
            extra={
                "event_name": "box.outbox.row.completed",
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "attempts": row.attempts or 1,
                "outcome": "success",
            },
        )
        return "done"

    @staticmethod
    def _parse_payload(row: BoxOutbox) -> Dict[str, Any]:
        if not row.payload:
            return {}
        try:
            parsed = json.loads(row.payload)
            if not isinstance(parsed, dict):
                raise ValueError("payload is not a JSON object")
            return parsed
        except json.JSONDecodeError as error:
            raise ValueError(f"payload is not valid JSON: {error}") from error

    def _handle_box_error(self, row: BoxOutbox, error: BoxError) -> str:
        """
        Same backoff math as MsOutboxWorker._handle_ms_error, with one
        accounting difference: ClaimNextPendingBoxOutbox increments Attempts
        at CLAIM time, so the claimed row's value already counts this
        attempt. Using ms's `+ 1` here would double-count and dead-letter
        one attempt early.
        """
        attempts_so_far = row.attempts or 1
        next_attempt = attempts_so_far + 1

        if not error.is_retryable:
            logger.warning(
                "box.outbox.row.non_retryable_failure",
                extra={
                    "event_name": "box.outbox.row.non_retryable_failure",
                    "outbox_public_id": row.public_id,
                    "error_class": type(error).__name__,
                    "box_error_code": error.code,
                    "http_status": error.http_status,
                },
            )
            self._dead_letter(row, f"{type(error).__name__}: {error}")
            return "dead_lettered"

        if next_attempt > self.MAX_ATTEMPTS:
            logger.error(
                "box.outbox.row.retry_exhausted",
                extra={
                    "event_name": "box.outbox.row.retry_exhausted",
                    "outbox_public_id": row.public_id,
                    "attempts": attempts_so_far,
                    "max_attempts": self.MAX_ATTEMPTS,
                    "error_class": type(error).__name__,
                },
            )
            self._dead_letter(row, f"Retries exhausted after {attempts_so_far}: {error}")
            return "dead_lettered"

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
            "box.outbox.row.retry_scheduled",
            extra={
                "event_name": "box.outbox.row.retry_scheduled",
                "outbox_public_id": row.public_id,
                "attempts": attempts_so_far,
                "next_attempt": next_attempt,
                "sleep_seconds": backoff_seconds,
                "next_retry_at": next_retry_at.isoformat(),
                "error_class": type(error).__name__,
                "box_error_code": error.code,
            },
        )
        return "failed"

    def _dead_letter(self, row: BoxOutbox, last_error: str) -> None:
        self.repo.mark_dead_letter(
            id=row.id,
            row_version=row.row_version,
            last_error=last_error,
        )
        logger.error(
            "box.outbox.row.dead_lettered",
            extra={
                "event_name": "box.outbox.row.dead_lettered",
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "last_error": last_error,
            },
        )

        # Escalation hook — failure-isolated: a broken reconciliation write
        # must never mask the dead-letter itself.
        try:
            self._escalate_dead_letter(row, last_error)
        except Exception:
            logger.exception(
                "box.outbox.dead_letter.escalation_failed",
                extra={
                    "event_name": "box.outbox.dead_letter.escalation_failed",
                    "outbox_public_id": row.public_id,
                },
            )

    def _escalate_dead_letter(self, row: BoxOutbox, last_error: str) -> None:
        """Create a BoxReconciliationIssue so the dead-letter isn't invisible."""
        # Only escalate for kinds where dropping silently would be harmful.
        if row.kind not in (KIND_UPLOAD_FILE, KIND_UPDATE_EXCEL):
            return

        from integrations.box.reconciliation.business.service import (
            BoxReconciliationIssueService,
        )

        payload = self._parse_payload(row) if row.payload else {}

        BoxReconciliationIssueService().flag_dead_letter(
            kind=row.kind,
            entity_type=row.entity_type,
            entity_public_id=row.entity_public_id,
            tenant_id=self._resolve_tenant_marker(),
            outbox_public_id=row.public_id,
            details=last_error,
            drive_item_id=payload.get("box_folder_id"),
            worksheet_name=None,
        )

    @staticmethod
    def _resolve_tenant_marker() -> str:
        """
        `[box].[ReconciliationIssue]` copies the MS column set verbatim,
        including `TenantId NOT NULL`. Box's closest analogue is the
        enterprise id; fall back to a literal marker when unconfigured.
        """
        try:
            import config

            enterprise_id = config.Settings().box_enterprise_id
            return str(enterprise_id) if enterprise_id else "box"
        except Exception:
            return "box"

    # ------------------------------------------------------------------ #
    # Per-kind handlers
    # ------------------------------------------------------------------ #

    def _handle_upload_box_file(
        self,
        row: BoxOutbox,
        payload: Dict[str, Any],
    ) -> None:
        """
        Upload a blob to Box. Payload shape (written by
        BoxOutboxService.enqueue_box_upload):

          {
            "blob_path": "attachments/<public_id>.pdf",  // Azure blob path
            "filename": "<sanitized, identity-embedded>",
            "content_type": "application/pdf",
            "box_folder_id": "1234567890",
            "attachment_id": 42,        // nullable
            "doc_kind": "attachment",
            "project_id": 7             // nullable
          }

        The row's entity identity is threaded into the payload before
        dispatch — `push_blob_to_box` needs `entity_type` /
        `entity_public_id` for its conflict-recovery registry guard
        (deciding whether a name collision is "our" file to re-version or a
        foreign file to dead-letter over).
        """
        required = ("blob_path", "filename", "content_type", "box_folder_id")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValueError(f"upload payload missing required fields: {missing}")

        payload["entity_type"] = row.entity_type
        payload["entity_public_id"] = row.entity_public_id

        # Lazy import: the file package is a sibling vertical; importing at
        # module load would couple outbox/ -> file/ for every consumer of
        # the worker (mirrors the MS worker's per-handler imports).
        from integrations.box.base.client import BoxHttpClient
        from integrations.box.file.business.service import BoxFileService

        with BoxHttpClient() as client:
            BoxFileService().push_blob_to_box(
                client=client,
                payload=payload,
                outbox_id=row.id,
                request_id=row.request_id,
                actor_user_id=row.created_by_user_id,
            )

    def _handle_update_box_excel(
        self,
        row: BoxOutbox,
        payload: Dict[str, Any],
    ) -> None:
        """
        Phase 3: update the DETAILS tab of a Box-hosted .xlsx. Payload shape
        (written by BoxOutboxService.enqueue_box_excel):

          {
            "box_file_id": "1234567890",
            "worksheet_name": "DETAILS",
            "entity_type": "bill" | "expense" | "bill_credit",
            "entity_public_id": "<uuid>",
            "project_id": 7
          }

        Unlike the upload handler, the heavy lifting (download → openpyxl edit →
        upload version) lives entirely inside the excel service, serialized by a
        Box file lock. Box has no cell-level API, so all read / idempotency /
        insertion / write happens at drain time here.
        """
        # Accept either a single-entity payload (entity_type + entity_public_id) or a
        # batch payload (entities: [{entity_type, entity_public_id}, ...], written by
        # BoxOutboxService.enqueue_box_excel_batch). Detailed branch validation also lives
        # in BoxExcelUpdateService.handle(); this outer gate mirrors it so batch rows are
        # NOT dead-lettered before delegation.
        if not payload.get("box_file_id"):
            raise ValueError("update_box_excel payload missing required fields: ['box_file_id']")
        entities = payload.get("entities")
        if entities is not None:
            if not isinstance(entities, list) or not entities:
                raise ValueError("update_box_excel batch payload has empty/invalid entities")
        elif not payload.get("entity_type") or not payload.get("entity_public_id"):
            raise ValueError(
                "update_box_excel payload missing required fields: ['entity_type', 'entity_public_id']"
            )

        # Lazy import: the excel package is a sibling vertical; importing at
        # module load would couple outbox/ -> excel/ for every consumer of the
        # worker (mirrors the upload handler's per-handler imports).
        from integrations.box.excel.business.service import BoxExcelUpdateService

        BoxExcelUpdateService().handle(row, payload)
