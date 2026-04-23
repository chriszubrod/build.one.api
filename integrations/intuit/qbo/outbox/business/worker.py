# Python Standard Library Imports
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

# Local Imports
from integrations.intuit.qbo.base.correlation import (
    ensure_correlation_id,
    idempotency_key_context,
    set_correlation_id,
)
from integrations.intuit.qbo.base.errors import QboError, QboSyncTokenMismatchError
from integrations.intuit.qbo.base.locking import qbo_app_lock
from integrations.intuit.qbo.base.retry import RetryPolicy, compute_backoff_seconds
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.persistence.repo import QboOutboxRepository

logger = logging.getLogger(__name__)


# Chapter 5 decision: dead-letter after 5 failed attempts. `attempts` on
# the row is incremented on each FailQboOutbox call; we check
# `attempts + 1 >= MAX_ATTEMPTS` before marking failed (would become
# dead-letter on the attempt after this).
MAX_ATTEMPTS = 5

# The drain lock name is stable across processes: only one drain loop
# (API process or standalone worker) holds this at a time. Prevents two
# workers from claiming the same row despite the SQL-side UPDLOCK+READPAST.
DRAIN_LOCK_NAME = "qbo_outbox_drain"

# How long to wait for the drain lock before giving up. Short: if another
# worker is draining, we just try again on the next tick.
DRAIN_LOCK_TIMEOUT_MS = 1000


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

    def __init__(self, repo: Optional[QboOutboxRepository] = None):
        self.repo = repo or QboOutboxRepository()
        # Kind → handler. Handlers take a QboOutbox row and perform the
        # actual QBO write. Each handler runs inside an idempotency_key
        # context so all POST/PUTs it issues carry the row's RequestId.
        self._dispatch_table: Dict[str, Callable[[QboOutbox], None]] = {
            "sync_bill_to_qbo": self._handle_sync_bill,
            "sync_expense_to_qbo": self._handle_sync_expense,
            "sync_invoice_to_qbo": self._handle_sync_invoice,
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
        with qbo_app_lock(DRAIN_LOCK_NAME, timeout_ms=DRAIN_LOCK_TIMEOUT_MS) as got_lock:
            if not got_lock:
                logger.debug("qbo.outbox.drain.skipped_lock_busy")
                return False

            row = self.repo.claim_next_pending()
            if not row:
                return False

            self._process(row)
            return True

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
    # Per-row processing
    # ------------------------------------------------------------------ #

    def _process(self, row: QboOutbox) -> None:
        """
        Dispatch a single claimed row. Installs correlation and idempotency
        context so downstream QBO calls log/tag with the row's IDs and use
        the stable RequestId as the requestid query param.
        """
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
            # Unexpected non-QboError — treat as non-retryable failure.
            logger.exception(
                "qbo.outbox.row.unexpected_error",
                extra={
                    "event_name": "qbo.outbox.row.unexpected_error",
                    "correlation_id": row.correlation_id,
                    "outbox_public_id": row.public_id,
                    "error_class": type(error).__name__,
                },
            )
            self._dead_letter(row, f"Unexpected {type(error).__name__}: {error}")
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

        if next_attempt > MAX_ATTEMPTS:
            logger.error(
                "qbo.outbox.row.retry_exhausted",
                extra={
                    "event_name": "qbo.outbox.row.retry_exhausted",
                    "correlation_id": row.correlation_id,
                    "outbox_public_id": row.public_id,
                    "attempts": attempts_so_far,
                    "max_attempts": MAX_ATTEMPTS,
                    "error_class": type(error).__name__,
                },
            )
            self._dead_letter(row, f"Retries exhausted after {attempts_so_far}: {error}")
            return

        # Retryable: schedule next attempt with backoff.
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
            "qbo.outbox.row.retry_scheduled",
            extra={
                "event_name": "qbo.outbox.row.retry_scheduled",
                "correlation_id": row.correlation_id,
                "outbox_public_id": row.public_id,
                "attempts": attempts_so_far,
                "next_attempt": next_attempt,
                "sleep_seconds": backoff_seconds,
                "next_retry_at": next_retry_at.isoformat(),
                "error_class": type(error).__name__,
                "qbo_fault_code": error.code,
            },
        )

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
        elif row.entity_type == "Expense":
            self._refresh_expense(row)
        elif row.entity_type == "Invoice":
            self._refresh_invoice(row)
        else:
            logger.warning(
                f"qbo.outbox.refresh.unsupported_entity_type: {row.entity_type}"
            )

    def _refresh_bill(self, row: QboOutbox) -> None:
        from integrations.intuit.qbo.bill.business.service import QboBillService
        from integrations.intuit.qbo.bill.connector.bill.persistence.repo import (
            BillBillRepository,
        )
        from integrations.intuit.qbo.bill.connector.bill.business.service import (
            BillBillConnector,
        )
        from integrations.intuit.qbo.bill.external.client import QboBillClient
        from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository
        from entities.bill.business.service import BillService

        bill = BillService().read_by_public_id(row.entity_public_id)
        if not bill:
            return

        mapping = BillBillRepository().read_by_bill_id(int(bill.id))
        if not mapping:
            return

        local_qbo_bill = QboBillRepository().read_by_id(mapping.qbo_bill_id)
        if not local_qbo_bill or not local_qbo_bill.qbo_id:
            return

        with QboBillClient(realm_id=row.realm_id) as client:
            fresh = client.get_bill(local_qbo_bill.qbo_id)
        refreshed_bill, refreshed_lines = QboBillService().upsert_from_external(
            fresh, row.realm_id
        )
        BillBillConnector().sync_from_qbo_bill(
            qbo_bill=refreshed_bill, qbo_bill_lines=refreshed_lines
        )

    def _refresh_expense(self, row: QboOutbox) -> None:
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
        from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import (
            PurchaseExpenseRepository,
        )
        from integrations.intuit.qbo.purchase.connector.expense.business.service import (
            PurchaseExpenseConnector,
        )
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
        from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository
        from entities.expense.business.service import ExpenseService

        expense = ExpenseService().read_by_public_id(row.entity_public_id)
        if not expense:
            return

        mapping = PurchaseExpenseRepository().read_by_expense_id(int(expense.id))
        if not mapping:
            return

        local_qbo_purchase = QboPurchaseRepository().read_by_id(mapping.qbo_purchase_id)
        if not local_qbo_purchase or not local_qbo_purchase.qbo_id:
            return

        with QboPurchaseClient(realm_id=row.realm_id) as client:
            fresh = client.get_purchase(local_qbo_purchase.qbo_id)
        refreshed_purchase, refreshed_lines = QboPurchaseService().upsert_from_external(
            fresh, row.realm_id
        )
        PurchaseExpenseConnector().sync_from_qbo_purchase(
            qbo_purchase=refreshed_purchase, qbo_purchase_lines=refreshed_lines
        )

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

    def _handle_sync_expense(self, row: QboOutbox) -> None:
        from entities.expense.business.service import ExpenseService
        from integrations.intuit.qbo.purchase.connector.expense.business.service import (
            PurchaseExpenseConnector,
        )

        expense = ExpenseService().read_by_public_id(row.entity_public_id)
        if not expense:
            raise ValueError(f"Expense not found for public_id {row.entity_public_id}")

        PurchaseExpenseConnector().sync_to_qbo_purchase(
            expense=expense,
            realm_id=row.realm_id,
        )

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
