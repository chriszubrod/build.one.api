# Python Standard Library Imports
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

# Local Imports
from integrations.intuit.qbo.base.correlation import (
    ensure_correlation_id,
    idempotency_key_context,
    set_correlation_id,
)
from integrations.intuit.qbo.base.budget import (
    BudgetStatus,
    QboApiBudget,
    get_qbo_api_budget,
    reset_at_for_month,
)
from integrations.intuit.qbo.base.client import writes_allowed
from integrations.intuit.qbo.base.errors import (
    QboBudgetExceededError,
    QboError,
    QboSyncTokenMismatchError,
    QboWriteRefusedError,
    is_retryable_error,
)
from integrations.intuit.qbo.base.locking import qbo_app_lock
from integrations.intuit.qbo.base.retry import RetryPolicy, compute_backoff_seconds
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.persistence.repo import QboOutboxRepository
from shared.authz.context import system_authz

logger = logging.getLogger(__name__)


# Chapter 5 decision: dead-letter after 5 failed attempts. `attempts` on
# the row is incremented on each FailQboOutbox call; we check
# `attempts + 1 >= MAX_ATTEMPTS` before marking failed (would become
# dead-letter on the attempt after this).
MAX_ATTEMPTS = 5

# Write-refused parks: 15 min between retries. Mid-handler defence-in-depth only —
# drain_once skips claiming entirely while external writes are blocked (budget
# breaker or ALLOW_QBO_WRITES off), so parks are rare (~flag flip mid-handler).
# A permanently-unset prod flag logs qbo.outbox.drain.skipped_writes_blocked
# (reason=writes_disabled) at ERROR on every tick, which is a better alarm than
# per-row dead-letters.
WRITE_REFUSED_PARK_PREFIX = "Parked: QBO writes disabled"
WRITE_REFUSED_PARK_INTERVAL = timedelta(minutes=15)

# The drain lock name is stable across processes: only one drain loop
# (API process or standalone worker) holds this at a time. Prevents two
# workers from claiming the same row despite the SQL-side UPDLOCK+READPAST.
DRAIN_LOCK_NAME = "qbo_outbox_drain"

# How long to wait for the drain lock before giving up. Short: if another
# worker is draining, we just try again on the next tick.
DRAIN_LOCK_TIMEOUT_MS = 1000

# 15 minutes — drain cadence is 5s in-process / 60s from the scheduler
# Function App, and the longest legitimate handler is _handle_sync_bill (bill
# push with a 30s retry budget plus a best-effort per-attachment loop). This
# is an order of magnitude above any realistic handler while still being vastly
# faster than 'never'. The numeric threshold is defense-in-depth only: the
# structural guarantee is that reclaim runs while holding the drain applock,
# which a live worker also holds.
DEFAULT_RECLAIM_AFTER_SECONDS = 900


@dataclass(frozen=True)
class WriteGateDecision:
    reason: Optional[str]  # "budget_blocked" | "writes_disabled" | None
    budget_status: BudgetStatus

    @property
    def allowed(self) -> bool:
        return self.reason is None


def can_worker_do_external_writes_now(api_budget: QboApiBudget) -> WriteGateDecision:
    """
    Single pre-claim decision point for the QBO outbox drain loop. Checks the
    monthly API budget breaker (U-211) first, then the ALLOW_QBO_WRITES
    dev-safety gate (U-218b) — precedence matches the pre-unification code,
    where a blocked budget short-circuited before writes_allowed() was ever
    consulted.
    """
    status = api_budget.status()
    if status.blocked:
        return WriteGateDecision(reason="budget_blocked", budget_status=status)
    if not writes_allowed():
        return WriteGateDecision(reason="writes_disabled", budget_status=status)
    return WriteGateDecision(reason=None, budget_status=status)


def reclaim_after_seconds() -> int:
    """Seconds before an in_progress row is considered stranded."""
    raw = os.getenv("QBO_OUTBOX_RECLAIM_AFTER_SECONDS", "").strip()
    if not raw:
        return DEFAULT_RECLAIM_AFTER_SECONDS
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_RECLAIM_AFTER_SECONDS
    except ValueError:
        return DEFAULT_RECLAIM_AFTER_SECONDS


class QboOutboxWorker:
    """
    Drain-loop for the QBO outbox. Intended to be called periodically by
    an APScheduler job (task #14e). Each tick:

      1. Acquires a cross-process drain lock (so only one worker drains).
      2. Claims the oldest ready row via ClaimNextPendingQboOutbox.
      3. Dispatches by `kind` to the appropriate handler.
      4. Marks the row done / failed / dead_letter based on the outcome.

    On retryable QboError the row is scheduled for retry with exponential
    backoff. After MAX_ATTEMPTS (5) or any non-retryable error, the row
    goes to dead_letter for human triage.
    """

    def __init__(
        self,
        repo: Optional[QboOutboxRepository] = None,
        api_budget: Optional[QboApiBudget] = None,
    ):
        self.repo = repo or QboOutboxRepository()
        self._api_budget = api_budget or get_qbo_api_budget()
        # Kind → handler. Handlers take a QboOutbox row and perform the
        # actual QBO write. Each handler runs inside an idempotency_key
        # context so all POST/PUTs it issues carry the row's RequestId.
        self._dispatch_table: Dict[str, Callable[[QboOutbox], None]] = {
            "sync_bill_to_qbo": self._handle_sync_bill,
            "sync_invoice_to_qbo": self._handle_sync_invoice,
            "recode_purchase_line": self._handle_recode_purchase_line,
        }
        # Retry policy for backoff computation. Reuses base/retry.py math.
        self._retry_policy = RetryPolicy.for_writes()

    # ------------------------------------------------------------------ #
    # Drain loop entry points
    # ------------------------------------------------------------------ #

    def drain_once(self) -> bool:
        """
        Claim and process at most one row. Returns True if a row was
        processed (successfully or not), False if nothing was ready or the
        drain lock couldn't be acquired.
        """
        decision = can_worker_do_external_writes_now(self._api_budget)
        if not decision.allowed:
            self._log_writes_blocked(decision)

        with qbo_app_lock(DRAIN_LOCK_NAME, timeout_ms=DRAIN_LOCK_TIMEOUT_MS) as got_lock:
            if not got_lock:
                logger.debug("qbo.outbox.drain.skipped_lock_busy")
                return False

            # Reclaim BEFORE the write gate is enforced (regardless of WHY writes
            # are currently blocked — budget breaker or ALLOW_QBO_WRITES off):
            # stranding is a DB-only condition and its repair (in_progress ->
            # pending) issues no QBO call, so leaving it behind either gate would
            # mean a deploy restart during a budget-blocked OR writes-off window
            # stranded rows that nothing would ever release.
            self._reclaim_stranded_rows()

            if not decision.allowed:
                return False

            row = self.repo.claim_next_pending()
            if not row:
                return False

            self._process(row)
            return True

    def _log_writes_blocked(self, decision: WriteGateDecision) -> None:
        # Mirrors QboApiBudget._log_band_crossing's shape (base/budget.py): pick
        # the log function once, single call site. No `else` needed to stay safe
        # against a future third reason — ERROR is the right default severity
        # for an unrecognized block reason (alarm loudly, don't warn quietly).
        extra = {
            "event_name": "qbo.outbox.drain.skipped_writes_blocked",
            "reason": decision.reason,
            "month_key": decision.budget_status.month_key,
            "call_count": decision.budget_status.call_count,
            "block_threshold": decision.budget_status.block_threshold,
        }
        log = logger.warning if decision.reason == "budget_blocked" else logger.error
        log("qbo.outbox.drain.skipped_writes_blocked", extra=extra)

    def drain_all(self, max_rows: int = 100) -> int:
        """
        Drain up to `max_rows` in a loop. Returns the count actually processed.
        Stops early when the queue is empty or the lock can't be acquired.
        """
        processed = 0
        while processed < max_rows:
            if not self.drain_once():
                break
            processed += 1
        return processed

    # ------------------------------------------------------------------ #
    # Stranded-row reclaim
    # ------------------------------------------------------------------ #

    def _reclaim_stranded_rows(self) -> None:
        """
        Reclaim rows stranded in 'in_progress' after a worker crash/restart.
        Fail-open: a reclaim failure (e.g. the sproc not yet applied in prod)
        must NEVER block the drain — falls back to pre-unit behavior.
        """
        try:
            stale_seconds = reclaim_after_seconds()
            stale_before = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
            reclaimed = self.repo.reclaim_stranded(
                stale_before=stale_before,
                max_attempts=MAX_ATTEMPTS,
            )
            for row in reclaimed:
                logger.warning(
                    "qbo.outbox.row.reclaimed_stranded",
                    extra={
                        "event_name": "qbo.outbox.row.reclaimed_stranded",
                        "outbox_public_id": row["public_id"],
                        "entity_type": row["entity_type"],
                        "entity_public_id": row["entity_public_id"],
                        "kind": row["kind"],
                        "attempts": row["attempts"],
                        "started_at": row["started_at"],
                        "new_status": row["status"],
                        "stale_after_seconds": stale_seconds,
                    },
                )
        except Exception:
            logger.exception("qbo.outbox.reclaim.failed")

    # ------------------------------------------------------------------ #
    # Per-row processing
    # ------------------------------------------------------------------ #

    def _process(self, row: QboOutbox) -> None:
        """
        Dispatch a single claimed row. Installs correlation and idempotency
        context so downstream QBO calls log/tag with the row's IDs and use
        the stable RequestId as the requestid query param.

        Drain workers process rows that span all users by design — assert
        system intent at the boundary via the shared `system_authz()`
        contextmanager so callers (HTTP endpoint, in-process scheduler,
        REPL) don't hand-roll save/restore. Prior context is restored on
        exit so we don't leak system-admin into whatever ran us.
        """
        with system_authz():
            self._process_inner(row)

    def _process_inner(self, row: QboOutbox) -> None:
        # Install correlation context from the row (if present) so all
        # downstream logs stitch together with the original request.
        if row.correlation_id:
            set_correlation_id(row.correlation_id)
        else:
            ensure_correlation_id()

        logger.info(
            "qbo.outbox.row.drained",
            extra={
                "event_name": "qbo.outbox.row.drained",
                "correlation_id": row.correlation_id,
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "realm_id": row.realm_id,
                "attempt": (row.attempts or 0) + 1,
            },
        )

        handler = self._dispatch_table.get(row.kind)
        if handler is None:
            self._dead_letter(row, f"Unknown outbox kind: {row.kind}")
            return

        try:
            # Thread the row's stable RequestId into every QBO write this
            # handler makes. On retry the same key is reused → QBO dedups.
            with idempotency_key_context(row.request_id):
                try:
                    handler(row)
                except QboSyncTokenMismatchError as error:
                    # Task #20: someone else updated this entity in QBO
                    # between our last pull and our push attempt. Pull
                    # fresh state (refreshes the local SyncToken cache)
                    # and retry the handler once. The idempotency-key
                    # context is still in scope so the retry uses the
                    # same RequestId.
                    logger.info(
                        "qbo.outbox.row.sync_token_mismatch",
                        extra={
                            "event_name": "qbo.outbox.row.sync_token_mismatch",
                            "correlation_id": row.correlation_id,
                            "outbox_public_id": row.public_id,
                            "entity_type": row.entity_type,
                            "entity_public_id": row.entity_public_id,
                            "qbo_fault_code": error.code,
                        },
                    )
                    self._refresh_from_qbo(row)
                    handler(row)
        except QboError as error:
            self._handle_qbo_error(row, error)
            return
        except Exception as error:
            self._handle_unexpected_error(row, error)
            return

        # Success
        self.repo.mark_done(id=row.id, row_version=row.row_version)
        logger.info(
            "qbo.outbox.row.completed",
            extra={
                "event_name": "qbo.outbox.row.completed",
                "correlation_id": row.correlation_id,
                "operation_name": row.kind,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "realm_id": row.realm_id,
                "attempts": (row.attempts or 0) + 1,
                "outcome": "success",
            },
        )

    def _handle_qbo_error(self, row: QboOutbox, error: QboError) -> None:
        """Decide whether to retry or dead-letter based on the error class."""
        attempts_so_far = (row.attempts or 0) + 1
        next_attempt = attempts_so_far + 1

        # U-211: a tripped monthly budget breaker is neither the row's fault
        # nor retryable-with-backoff in-month — park the row until the cap
        # resets on the 1st (UTC). Never dead-letter on budget: the row is
        # perfectly healthy; only the calendar is against it. Checked before
        # the is_retryable branch because the error is is_retryable=False
        # (so the in-process retry loop won't spin), yet dead-letter would
        # be wrong.
        if isinstance(error, QboBudgetExceededError):
            reset_at = reset_at_for_month(error.month_key)
            logger.warning(
                "qbo.outbox.row.parked_budget_blocked",
                extra={
                    "event_name": "qbo.outbox.row.parked_budget_blocked",
                    "correlation_id": row.correlation_id,
                    "outbox_public_id": row.public_id,
                    "month_key": error.month_key,
                    "call_count": error.call_count,
                    "budget": error.budget,
                    "next_retry_at": reset_at.isoformat(),
                },
            )
            self.repo.mark_failed(
                id=row.id,
                row_version=row.row_version,
                next_retry_at=reset_at,
                last_error=f"Parked: monthly QBO API budget exhausted ({error})",
            )
            return

        if isinstance(error, QboWriteRefusedError):
            # Defence-in-depth for a flag that flips mid-handler (same category
            # as budget exceeded: refused locally before any byte left the process).
            next_retry_at = datetime.now(timezone.utc) + WRITE_REFUSED_PARK_INTERVAL
            logger.error(
                "qbo.outbox.row.parked_write_refused",
                extra={
                    "event_name": "qbo.outbox.row.parked_write_refused",
                    "correlation_id": row.correlation_id,
                    "outbox_public_id": row.public_id,
                    "attempt": attempts_so_far,
                    "next_retry_at": next_retry_at.isoformat(),
                },
            )
            self.repo.mark_failed(
                id=row.id,
                row_version=row.row_version,
                next_retry_at=next_retry_at,
                last_error=f"{WRITE_REFUSED_PARK_PREFIX} ({error})",
            )
            return

        if not error.is_retryable:
            logger.warning(
                "qbo.outbox.row.non_retryable_failure",
                extra={
                    "event_name": "qbo.outbox.row.non_retryable_failure",
                    "correlation_id": row.correlation_id,
                    "outbox_public_id": row.public_id,
                    "error_class": type(error).__name__,
                    "qbo_fault_code": error.code,
                    "http_status": error.http_status,
                },
            )
            self._dead_letter(row, f"{type(error).__name__}: {error}")
            return

        self._schedule_retry_or_dead_letter(
            row,
            last_error=f"{type(error).__name__}: {error}",
            error_class_name=type(error).__name__,
            retry_after_seconds=error.retry_after_seconds,
            extra_log_fields={"qbo_fault_code": error.code},
        )

    def _handle_unexpected_error(self, row: QboOutbox, error: Exception) -> None:
        """
        Retry or dead-letter a non-QboError handler failure.

        Asymmetry: a wrong retry costs at most MAX_ATTEMPTS bounded,
        idempotency-keyed attempts (a QBO call that already landed dedups via
        the row's RequestId rather than duplicating); a wrong dead-letter
        permanently strands a money-path push until a human runs
        scripts/retry_qbo_outbox_dead_letters.py — so an unknown/unclassified
        exception should retry. See the three-rule policy below (mirrored from
        base/sync_outcome.py::record_projection_error, the other caller of
        is_retryable_error) for how "unknown" is distinguished from
        "confirmed permanent."
        """
        logger.exception(
            "qbo.outbox.row.unexpected_error",
            extra={
                "event_name": "qbo.outbox.row.unexpected_error",
                "correlation_id": row.correlation_id,
                "outbox_public_id": row.public_id,
                "error_class": type(error).__name__,
            },
        )
        last_error = f"Unexpected {type(error).__name__}: {error}"

        # Mirrors base/sync_outcome.py::record_projection_error's hold-vs-skip policy
        # for this same classifier: retryable errors retry; a plain ValueError is the
        # connectors' permanent-data-issue convention and dead-letters; anything else
        # is unrecognized and, per the retry/dead-letter asymmetry above, retries
        # rather than risk permanently stranding a transient failure that simply
        # didn't match a known transient signature.
        permanent_data_issue = isinstance(error, ValueError) and not is_retryable_error(error)
        if permanent_data_issue:
            self._dead_letter(row, last_error)
            return

        self._schedule_retry_or_dead_letter(
            row,
            last_error=last_error,
            error_class_name=type(error).__name__,
        )

    def _schedule_retry_or_dead_letter(
        self,
        row: QboOutbox,
        *,
        last_error: str,
        error_class_name: str,
        retry_after_seconds: Optional[float] = None,
        extra_log_fields: Optional[dict] = None,
    ) -> None:
        """
        Schedule the next attempt with backoff, or dead-letter once MAX_ATTEMPTS
        is exhausted. Shared tail for every already-decided-retryable failure
        (QboError.is_retryable, and _handle_unexpected_error's classification).
        """
        attempts_so_far = (row.attempts or 0) + 1
        next_attempt = attempts_so_far + 1

        if next_attempt > MAX_ATTEMPTS:
            logger.error(
                "qbo.outbox.row.retry_exhausted",
                extra={
                    "event_name": "qbo.outbox.row.retry_exhausted",
                    "correlation_id": row.correlation_id,
                    "outbox_public_id": row.public_id,
                    "attempts": attempts_so_far,
                    "max_attempts": MAX_ATTEMPTS,
                    "error_class": error_class_name,
                },
            )
            self._dead_letter(row, f"Retries exhausted after {attempts_so_far}: {last_error}")
            return

        backoff_seconds = compute_backoff_seconds(
            attempt=attempts_so_far,
            policy=self._retry_policy,
            retry_after_seconds=retry_after_seconds,
        )
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)

        self.repo.mark_failed(
            id=row.id,
            row_version=row.row_version,
            next_retry_at=next_retry_at,
            last_error=last_error,
        )
        log_fields = {
            "event_name": "qbo.outbox.row.retry_scheduled",
            "correlation_id": row.correlation_id,
            "outbox_public_id": row.public_id,
            "attempts": attempts_so_far,
            "next_attempt": next_attempt,
            "sleep_seconds": backoff_seconds,
            "next_retry_at": next_retry_at.isoformat(),
            "error_class": error_class_name,
        }
        if extra_log_fields:
            log_fields.update(extra_log_fields)
        logger.warning("qbo.outbox.row.retry_scheduled", extra=log_fields)

    def _dead_letter(self, row: QboOutbox, last_error: str) -> None:
        self.repo.mark_dead_letter(
            id=row.id,
            row_version=row.row_version,
            last_error=last_error,
        )
        logger.error(
            "qbo.outbox.row.dead_lettered",
            extra={
                "event_name": "qbo.outbox.row.dead_lettered",
                "correlation_id": row.correlation_id,
                "outbox_public_id": row.public_id,
                "entity_type": row.entity_type,
                "entity_public_id": row.entity_public_id,
                "last_error": last_error,
            },
        )

    # ------------------------------------------------------------------ #
    # Per-kind handlers
    #
    # These are deliberately thin: load the local entity, dispatch to the
    # existing connector's sync_to_qbo_* method. The connector's internal
    # client calls inherit the row's RequestId from the
    # idempotency_key_context set by _process().
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Conflict recovery (task #20)
    # ------------------------------------------------------------------ #

    def _refresh_from_qbo(self, row: QboOutbox) -> None:
        """
        Re-pull the entity from QBO into the local cache so the SyncToken
        is current before a retry push.

        Called when the handler hit a SyncToken mismatch — someone else
        updated the record in QBO. After this runs, the local `qbo.Bill`
        (or equivalent) row holds the latest SyncToken and the next push
        attempt won't be rejected as stale.

        If the re-pull itself fails, the exception propagates to the
        caller, which treats it as a handler failure — the outbox row's
        normal retry/dead-letter logic applies.
        """
        if row.entity_type == "Bill":
            self._refresh_bill(row)
        elif row.entity_type == "Invoice":
            self._refresh_invoice(row)
        else:
            logger.warning(
                f"qbo.outbox.refresh.unsupported_entity_type: {row.entity_type}"
            )

    def _refresh_bill(self, row: QboOutbox) -> None:
        """
        U-355: dbo.Bill.QboId/RealmId (U-238a) is the sole identity store now
        that qbo.BillBill is retired -- verify_identity_dbo_only (a fresh
        dbo-only re-read that must resolve back to the same Bill.id) replaces
        the retired verify_bill_qbo_identity wrapper, mirroring every other
        push-side identity check in this package post-Wave-5 (e.g.
        BillBillConnector._get_qbo_vendor_ref).

        A falsy bill.qbo_id means the Bill has never been pushed -- nothing to
        refresh, plain return (there is no legacy mapping-table hop left to
        fall back to). On a GENUINE conflict (dbo.Bill.QboId no longer
        resolves back to this same Bill on a fresh read -- a stale/reassigned
        identity), refuses to guess: records a bill_identity_conflict
        ReconciliationIssue and RAISES (never a silent return) — mirroring
        base/identity_fastpath.py's own hard-stop discipline. Silently
        returning here is not equivalent-but-safer: BillBillConnector.
        sync_to_qbo_bill (the handler being retried) is create-only and
        short-circuits to a no-op success the instant dbo.Bill.QboId verifies
        — exactly the precondition a hard-refuse requires — so a silent
        return here would let the retried push complete as "done" with the
        underlying conflict never surfaced anywhere but a single (possibly
        failed) ReconciliationIssue insert. Raising forces _process_inner's
        outer exception handling to decide the outcome explicitly instead. A
        GET refresh can't directly corrupt QBO, but the local cache it feeds
        (via upsert_from_external) can — that's the risk this hard-refuse
        exists to close (Chris's call, 2026-08-22, unchanged by this repoint).
        """
        from integrations.intuit.qbo.bill.business.service import QboBillService
        from integrations.intuit.qbo.bill.connector.bill.business.service import (
            BillBillConnector,
        )
        from integrations.intuit.qbo.bill.external.client import QboBillClient
        from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
        from entities.bill.business.service import BillService

        bill_service = BillService()
        bill = bill_service.read_by_public_id(row.entity_public_id)
        if not bill:
            return

        if not bill.qbo_id:
            return

        verified = verify_identity_dbo_only(
            bill, read_direct_by_qbo_identity=bill_service.read_by_qbo_identity,
        )
        if not verified:
            # bill.qbo_id was truthy but the fresh dbo-only re-read no longer
            # resolves back to this same Bill — hard-refuse. _record_bill_identity_
            # conflict always raises (see its own docstring for why a plain return
            # here would be wrong), so nothing follows this call.
            self._record_bill_identity_conflict(row, bill)

        with QboBillClient(realm_id=row.realm_id) as client:
            fresh = client.get_bill(verified)
        refreshed_bill, refreshed_lines = QboBillService().upsert_from_external(
            fresh, row.realm_id
        )
        BillBillConnector().sync_from_qbo_bill(
            qbo_bill=refreshed_bill, qbo_bill_lines=refreshed_lines
        )

    def _record_bill_identity_conflict(self, row: QboOutbox, bill) -> None:
        """Record the conflict and raise ValueError (never a silent return —
        see _refresh_bill's docstring for why). The raise below explicitly
        severs __context__ via a catch-and-reraise, NOT via `raise ... from
        None` alone — see the comment at that line for why `from None` isn't
        sufficient here.
        """
        from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
        from integrations.intuit.qbo.reconciliation.persistence.repo import (
            ReconciliationIssueRepository,
        )

        # NB: drift_type must be a string literal here, not a drift_types.py
        # constant reference — tests/test_qbo_reconciliation_recorder.py's
        # AST-based width guard statically resolves this argument and cannot
        # follow an imported name (same convention BillBillConnector's own
        # "bill_identity_conflict" call site already follows).
        record_mapping_issue(
            ReconciliationIssueRepository(),
            drift_type="bill_identity_conflict",
            entity_type="Bill",
            entity_public_id=row.entity_public_id,
            qbo_id=bill.qbo_id,
            realm_id=row.realm_id or "",
            details=(
                f"Outbox refresh for Bill {bill.id} found a genuine identity conflict: "
                f"dbo.Bill.QboId={bill.qbo_id!r} no longer resolves back to Bill {bill.id} "
                f"on a fresh dbo-only read (see verify_identity_dbo_only) — the identity was "
                f"reassigned to a different Bill. Refusing to refresh with disputed identity "
                f"— dead-lettering outbox row {row.public_id} immediately rather than risk "
                f"corrupting the local Bill cache. Investigate which side is correct."
            ),
        )
        # `from None` alone is NOT enough here, and setting .__context__ on
        # the exception object before `raise` isn't either — `raise` itself
        # re-derives __context__ from whatever exception is currently being
        # handled at the moment it executes (confirmed by direct test), which
        # is the QboSyncTokenMismatchError that got us into this method in the
        # first place, several frames up in _process_inner. is_retryable_error
        # (base/errors.py) walks __context__ explicitly regardless of `from
        # None`'s __suppress_context__ flag (that flag only affects traceback
        # PRINTING), so without truly severing it, that error's
        # is_retryable=True leaks through and misclassifies this ValueError as
        # retryable — scheduling backoff retries (each re-recording a
        # duplicate ReconciliationIssue) instead of the immediate dead-letter
        # this hard-refuse requires. The only way to make __context__ stick is
        # to catch our own raise immediately and re-raise the SAME object —
        # a bare `raise` re-propagates as-is without re-deriving __context__.
        conflict_error = ValueError(
            f"Bill {bill.id} identity conflict — dbo.Bill.QboId no longer resolves back to "
            f"this Bill on a fresh dbo-only read. See the recorded bill_identity_conflict "
            f"ReconciliationIssue for detail."
        )
        try:
            raise conflict_error
        except ValueError:
            conflict_error.__context__ = None
            raise

    # U-301b-deferred (Chris's Gate-1 call, 2026-08-22): _refresh_invoice below
    # is deliberately left on the legacy qbo.Invoice two-hop, unlike its sibling
    # _refresh_bill above. Invoice's equivalent outbox sync kind
    # (sync_invoice_to_qbo) has ZERO live rows ever (pushes are disabled per
    # CLAUDE.md), so there is no live traffic to equivalence-prove a repoint
    # against — unlike Bill's 918 live sync_bill_to_qbo rows. Repoint the same
    # way (dbo-native fast path + verify_invoice_qbo_identity wrapper on
    # identity_consistency.py's shared engine) once/if this push is re-enabled
    # and carries real traffic to verify against. Tracked in TODO.md under
    # "U-301b-deferred". (Expense's own _refresh_expense sibling was retired
    # U-354 along with the rest of its dead push path — sync_expense_to_qbo
    # never had a producer either, so this branch was equally unreachable;
    # see qbo.PurchaseExpense mapping-table retirement.)
    def _refresh_invoice(self, row: QboOutbox) -> None:
        from integrations.intuit.qbo.invoice.business.service import QboInvoiceService
        from integrations.intuit.qbo.invoice.connector.invoice.persistence.repo import (
            InvoiceInvoiceRepository,
        )
        from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
            InvoiceInvoiceConnector,
        )
        from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient
        from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository
        from entities.invoice.business.service import InvoiceService

        invoice = InvoiceService().read_by_public_id(row.entity_public_id)
        if not invoice:
            return

        mapping = InvoiceInvoiceRepository().read_by_invoice_id(int(invoice.id))
        if not mapping:
            return

        local_qbo_invoice = QboInvoiceRepository().read_by_id(mapping.qbo_invoice_id)
        if not local_qbo_invoice or not local_qbo_invoice.qbo_id:
            return

        with QboInvoiceClient(realm_id=row.realm_id) as client:
            fresh = client.get_invoice(local_qbo_invoice.qbo_id)
        refreshed_invoice, refreshed_lines = QboInvoiceService().upsert_from_external(
            fresh, row.realm_id
        )
        InvoiceInvoiceConnector().sync_from_qbo_invoice(
            qbo_invoice=refreshed_invoice, qbo_invoice_lines=refreshed_lines
        )

    def _handle_sync_bill(self, row: QboOutbox) -> None:
        # Lazy imports to avoid heavyweight chains at module load.
        from entities.bill.business.service import BillService

        bill_service = BillService()
        bill = bill_service.read_by_public_id(row.entity_public_id)
        if not bill:
            raise ValueError(f"Bill not found for public_id {row.entity_public_id}")

        # push_to_qbo handles both the bill push and the attachment sync,
        # and raises QboError on failure — the worker's outer handler
        # translates that into retry / dead-letter decisions.
        bill_service.push_to_qbo(bill=bill, realm_id=row.realm_id)

    def _handle_sync_invoice(self, row: QboOutbox) -> None:
        from entities.invoice.business.service import InvoiceService
        from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
            InvoiceInvoiceConnector,
        )

        invoice = InvoiceService().read_by_public_id(row.entity_public_id)
        if not invoice:
            raise ValueError(f"Invoice not found for public_id {row.entity_public_id}")

        InvoiceInvoiceConnector().sync_to_qbo_invoice(
            invoice=invoice,
            realm_id=row.realm_id,
        )

    def _handle_recode_purchase_line(self, row: QboOutbox) -> None:
        from entities.expense_coding_item.business.service import ExpenseCodingItemService
        from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
        from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
            PurchaseChangedInQboError,
            PurchaseRecodeMappingError,
        )
        from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError

        svc = ExpenseCodingItemService()
        item = svc.read_by_public_id(row.entity_public_id)
        if item is None:
            return
        # An outbox row only exists when a write was intended (confirm enqueued it
        # under ALLOW_QBO_WRITES). Process both 'enqueued' and 'confirmed': the
        # latter covers the crash window where enqueue succeeded but the follow-up
        # mark_enqueued did not — skipping it here would silently drop the write.
        # Terminal states (written / changed_in_qbo / error) are idempotent skips.
        if item.status not in ("enqueued", "confirmed"):
            return

        try:
            result = PurchaseExpenseConnector().recode_purchase_line(
                realm_id=row.realm_id,
                qbo_purchase_qbo_id=item.qbo_purchase_qbo_id,
                target_qbo_line_id=item.qbo_line_id,
                sub_cost_code_id=item.confirmed_sub_cost_code_id,
                project_id=item.confirmed_project_id,
                description=item.confirmed_description,
                expected_sync_token=item.sync_token_at_suggest or "",
            )
        except (PurchaseChangedInQboError, QboSyncTokenMismatchError):
            # Both mean the live Purchase drifted from the coding decision — bounce
            # to re-review, fail closed (never reach the worker's refresh-and-retry).
            svc.mark_changed_in_qbo(row.entity_public_id)
            return
        except PurchaseRecodeMappingError as exc:
            svc.mark_error(row.entity_public_id, write_error=str(exc))
            return

        status = result.get("status")
        if status in ("written", "already_recoded"):
            svc.mark_written(row.entity_public_id, sync_token=result.get("sync_token"))
        elif status == "line_not_found":
            svc.mark_changed_in_qbo(row.entity_public_id)
        else:
            svc.mark_error(
                row.entity_public_id,
                write_error=f"unexpected recode status: {status}",
            )
