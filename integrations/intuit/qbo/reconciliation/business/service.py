# Python Standard Library Imports
import logging
import os
import uuid
from typing import Optional

# Local Imports
from integrations.intuit.qbo.reconciliation.persistence.repo import (
    ReconciliationIssueRepository,
)

logger = logging.getLogger(__name__)


# Drift-type taxonomy. Each value carries an implied severity tier for
# the tiered auto-fix/flag policy (Chapter 5).
#
# - low   → auto-fixable; service applies the fix and writes the issue for audit.
# - medium → flagged; operator reviews and decides.
# - high   → flagged; human judgment required (never auto-fix).
DRIFT_QBO_MISSING_LOCALLY = "qbo_missing_locally"    # low — pull it
DRIFT_LOCAL_MISSING_QBO = "local_missing_qbo"        # medium — could be user-deleted locally OR QBO-deleted
DRIFT_STALE_SYNC_TOKEN = "stale_sync_token"          # low — pull + refresh cache
DRIFT_MISSING_MAPPING = "missing_mapping"            # low — create mapping row
DRIFT_FIELD_MISMATCH = "field_mismatch"              # medium — needs field-level source-of-truth rules (task #19)
DRIFT_DUPLICATE_MAPPING = "duplicate_mapping"        # high — data bug; never auto-unlink
DRIFT_QBO_VOIDED = "qbo_voided"                      # low — mark local as void (task #21 work)
DRIFT_INVOICE_DRAW_MISMATCH = "invoice_draw_mismatch"  # medium — customer-invoice ↔ QBO/billing-state drift


SEVERITY_BY_DRIFT = {
    DRIFT_QBO_MISSING_LOCALLY: "low",
    DRIFT_LOCAL_MISSING_QBO: "medium",
    DRIFT_STALE_SYNC_TOKEN: "low",
    DRIFT_MISSING_MAPPING: "low",
    DRIFT_FIELD_MISMATCH: "medium",
    DRIFT_DUPLICATE_MAPPING: "high",
    DRIFT_QBO_VOIDED: "low",
    DRIFT_INVOICE_DRAW_MISMATCH: "medium",
}


class ReconciliationService:
    """
    Detect and record drift between local DB and QBO.

    The service is a framework plus one implemented detector. Additional
    detectors plug in as separate `reconcile_*` methods. All detectors
    share the same issue-writing machinery via `_record_issue`.

    Current scope (minimum viable for task #16):
      - `reconcile_bill_qbo_missing_locally` — for each QBO Bill, if we
        don't have a local mapping, pull it into the local DB and record
        an auto-fix issue for the audit trail.

    Future detectors (stubs below — document the interface):
      - stale_sync_token detection
      - duplicate_mapping detection
      - field_mismatch detection (requires #19)
      - qbo_voided detection (requires #21)
      - local_missing_qbo detection
    """

    def __init__(self, repo: Optional[ReconciliationIssueRepository] = None):
        self.repo = repo or ReconciliationIssueRepository()

    # ------------------------------------------------------------------ #
    # Public reconcile entry points (one per entity type)
    # ------------------------------------------------------------------ #

    def reconcile_bills(self, realm_id: str) -> dict:
        """
        Full-scan reconciliation for Bills.

        Detectors run in this order:
          1. qbo_missing_locally — auto-fix (pull)
          2. qbo_voided — flag local Bills whose QBO counterpart no longer exists
          # TODO (future): local_missing_qbo, stale_sync_token, duplicate_mapping,
          #                field_mismatch

        Returns a summary dict suitable for structured logging.
        """
        run_id = str(uuid.uuid4())
        logger.info(
            "qbo.reconcile.run.started",
            extra={
                "event_name": "qbo.reconcile.run.started",
                "operation_name": "qbo.reconcile.bill",
                "entity_type": "Bill",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
            },
        )

        counts = {"auto_fixed": 0, "flagged": 0, "errors": 0}

        # Detector 1: QBO-missing-locally
        try:
            d1 = self._reconcile_bill_qbo_missing_locally(
                realm_id=realm_id, run_id=run_id
            )
            for key in ("auto_fixed", "flagged", "errors"):
                counts[key] += d1.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "bill_qbo_missing_locally",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        # Detector 2: QBO-voided detection (task #21)
        try:
            d2 = self._reconcile_bill_qbo_voided(
                realm_id=realm_id, run_id=run_id
            )
            for key in ("auto_fixed", "flagged", "errors"):
                counts[key] += d2.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "bill_qbo_voided",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        logger.info(
            "qbo.reconcile.run.completed",
            extra={
                "event_name": "qbo.reconcile.run.completed",
                "operation_name": "qbo.reconcile.bill",
                "entity_type": "Bill",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
                "auto_fixed": counts["auto_fixed"],
                "flagged": counts["flagged"],
                "errors": counts["errors"],
            },
        )
        return {"run_id": run_id, **counts}

    def reconcile_purchases(self, realm_id: str) -> dict:
        """
        Full-scan reconciliation for Purchases (Expenses).

        Detectors run in this order:
          1. qbo_missing_locally — auto-fix (pull)
          2. qbo_voided — flag local Expenses whose QBO counterpart no longer exists
        """
        run_id = str(uuid.uuid4())
        logger.info(
            "qbo.reconcile.run.started",
            extra={
                "event_name": "qbo.reconcile.run.started",
                "operation_name": "qbo.reconcile.purchase",
                "entity_type": "Purchase",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
            },
        )

        counts = {"auto_fixed": 0, "flagged": 0, "errors": 0}

        try:
            d1 = self._reconcile_purchase_qbo_missing_locally(
                realm_id=realm_id, run_id=run_id
            )
            for key in ("auto_fixed", "flagged", "errors"):
                counts[key] += d1.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "purchase_qbo_missing_locally",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        try:
            d2 = self._reconcile_purchase_qbo_voided(
                realm_id=realm_id, run_id=run_id
            )
            for key in ("auto_fixed", "flagged", "errors"):
                counts[key] += d2.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "purchase_qbo_voided",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        logger.info(
            "qbo.reconcile.run.completed",
            extra={
                "event_name": "qbo.reconcile.run.completed",
                "operation_name": "qbo.reconcile.purchase",
                "entity_type": "Purchase",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
                "auto_fixed": counts["auto_fixed"],
                "flagged": counts["flagged"],
                "errors": counts["errors"],
            },
        )
        return {"run_id": run_id, **counts}

    def reconcile_vendor_credits(self, realm_id: str) -> dict:
        """
        Full-scan reconciliation for VendorCredits (BillCredits).

        Detectors run in this order:
          1. qbo_missing_locally — auto-fix (pull)
          2. qbo_voided — flag local BillCredits whose QBO counterpart no longer exists
        """
        run_id = str(uuid.uuid4())
        logger.info(
            "qbo.reconcile.run.started",
            extra={
                "event_name": "qbo.reconcile.run.started",
                "operation_name": "qbo.reconcile.vendor_credit",
                "entity_type": "VendorCredit",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
            },
        )

        counts = {"auto_fixed": 0, "flagged": 0, "errors": 0}

        try:
            d1 = self._reconcile_vendor_credit_qbo_missing_locally(
                realm_id=realm_id, run_id=run_id
            )
            for key in ("auto_fixed", "flagged", "errors"):
                counts[key] += d1.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "vendor_credit_qbo_missing_locally",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        try:
            d2 = self._reconcile_vendor_credit_qbo_voided(
                realm_id=realm_id, run_id=run_id
            )
            for key in ("auto_fixed", "flagged", "errors"):
                counts[key] += d2.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "vendor_credit_qbo_voided",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        logger.info(
            "qbo.reconcile.run.completed",
            extra={
                "event_name": "qbo.reconcile.run.completed",
                "operation_name": "qbo.reconcile.vendor_credit",
                "entity_type": "VendorCredit",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
                "auto_fixed": counts["auto_fixed"],
                "flagged": counts["flagged"],
                "errors": counts["errors"],
            },
        )
        return {"run_id": run_id, **counts}

    def reconcile_invoice_draws(self, realm_id: str) -> dict:
        """
        Daily DB-side invariant check for customer invoices (the InvoiceAgent
        reconciliation invariant, checked between runs):

        For every QBO-mapped dbo.Invoice:
          1. dbo.Invoice.TotalAmount == qbo.Invoice.TotalAmt (±0.01)
          2. dbo.InvoiceLineItem count == qbo.InvoiceLine count
          3. Completed (IsDraft=0) invoices have no unlinked ('Manual') lines
          4. Completed invoices' source-linked lines all have IsBilled=1

        Pure SQL — no Graph/QBO calls — so drift from QBO-side edits or missed
        Step-8 runs is flagged within a day instead of at the next invoice run.
        Writes AT MOST ONE summary issue per run (never per-invoice rows — the
        legacy pull corpus is all-Manual by construction and would flood the
        table daily). Never auto-fixes: billing state is human territory.
        """
        from shared.database import get_connection

        run_id = str(uuid.uuid4())
        counts = {"auto_fixed": 0, "flagged": 0, "errors": 0}
        logger.info(
            "qbo.reconcile.run.started",
            extra={
                "event_name": "qbo.reconcile.run.started",
                "operation_name": "qbo.reconcile.invoice_draw",
                "entity_type": "Invoice",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
            },
        )
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT i.Id, CAST(i.PublicId AS NVARCHAR(50)) AS PublicId,
                           i.InvoiceNumber, i.TotalAmount, i.IsDraft,
                           qi.QboId, qi.TotalAmt,
                           (SELECT COUNT(*) FROM dbo.InvoiceLineItem x WHERE x.InvoiceId = i.Id) AS DboLines,
                           (SELECT COUNT(*) FROM qbo.InvoiceLine ql WHERE ql.QboInvoiceId = qi.Id) AS QboLines,
                           (SELECT COUNT(*) FROM dbo.InvoiceLineItem x
                              WHERE x.InvoiceId = i.Id AND x.SourceType = 'Manual') AS ManualLines,
                           (SELECT COUNT(*) FROM dbo.InvoiceLineItem x
                              LEFT JOIN dbo.BillLineItem b ON b.Id = x.BillLineItemId
                              LEFT JOIN dbo.ExpenseLineItem e ON e.Id = x.ExpenseLineItemId
                              LEFT JOIN dbo.BillCreditLineItem c ON c.Id = x.BillCreditLineItemId
                            WHERE x.InvoiceId = i.Id
                              AND x.SourceType IN ('BillLineItem','ExpenseLineItem','BillCreditLineItem')
                              AND COALESCE(b.IsBilled, e.IsBilled, c.IsBilled, 0) = 0) AS UnbilledSources
                    FROM qbo.InvoiceInvoice map
                    JOIN dbo.Invoice i ON i.Id = map.InvoiceId
                    JOIN qbo.Invoice qi ON qi.Id = map.QboInvoiceId
                    WHERE qi.RealmId = ?
                    """,
                    realm_id,
                )
                # Anti-flood: this detector writes AT MOST ONE summary issue per
                # run (the reconcile_bills pattern — a per-invoice flag here would
                # re-insert one row per invoice per day with no dedupe, and the
                # legacy pull corpus is all-Manual by construction, so the
                # unlinked/unbilled invariants match hundreds of historical
                # invoices that were never run through the reconciliation flow).
                qbo_drift = []          # invariants 1+2 — real QBO divergence, per-invoice detail
                unlinked_invoices = 0   # invariant 3 — aggregate only (legacy corpus is noisy)
                unbilled_invoices = 0   # invariant 4 — aggregate only
                for row in cursor.fetchall():
                    dbo_total = float(row.TotalAmount) if row.TotalAmount is not None else 0.0
                    qbo_total = float(row.TotalAmt) if row.TotalAmt is not None else 0.0
                    invoice_problems = []
                    if abs(dbo_total - qbo_total) >= 0.01:
                        invoice_problems.append(f"total dbo={dbo_total:.2f} qbo={qbo_total:.2f}")
                    if row.DboLines != row.QboLines:
                        invoice_problems.append(f"lines dbo={row.DboLines} qbo={row.QboLines}")
                    if invoice_problems:
                        qbo_drift.append(f"{row.InvoiceNumber} ({', '.join(invoice_problems)})")
                    if not row.IsDraft and row.ManualLines:
                        unlinked_invoices += 1
                    if not row.IsDraft and row.UnbilledSources:
                        unbilled_invoices += 1

                if qbo_drift or unlinked_invoices or unbilled_invoices:
                    counts["flagged"] += 1
                    drift_head = qbo_drift[:20]
                    drift_txt = (
                        f"QBO drift on {len(qbo_drift)} invoice(s): " + "; ".join(drift_head)
                        + (f"; and {len(qbo_drift) - 20} more" if len(qbo_drift) > 20 else "")
                    ) if qbo_drift else "no QBO total/line drift"
                    self._record_issue(
                        drift_type=DRIFT_INVOICE_DRAW_MISMATCH,
                        action="flagged",
                        entity_type="Invoice",
                        realm_id=realm_id,
                        details=(
                            f"Daily invoice-draw summary: {drift_txt}. "
                            f"Completed invoices with unlinked (Manual) lines: {unlinked_invoices}. "
                            f"Completed invoices with un-billed source lines: {unbilled_invoices}."
                        ),
                        reconcile_run_id=run_id,
                        severity_override="low" if not qbo_drift else None,
                    )
        except Exception:
            logger.exception(
                "qbo.reconcile.detector.failed",
                extra={"detector": "invoice_draw_mismatch", "reconcile_run_id": run_id},
            )
            counts["errors"] += 1

        logger.info(
            "qbo.reconcile.run.completed",
            extra={
                "event_name": "qbo.reconcile.run.completed",
                "operation_name": "qbo.reconcile.invoice_draw",
                "entity_type": "Invoice",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
                "auto_fixed": counts["auto_fixed"],
                "flagged": counts["flagged"],
                "errors": counts["errors"],
            },
        )
        return {"run_id": run_id, **counts}

    # ------------------------------------------------------------------ #
    # Concrete detectors
    # ------------------------------------------------------------------ #

    def _reconcile_bill_qbo_missing_locally(self, realm_id: str, run_id: str) -> dict:
        """
        Full-scan QBO for all Bills. For any QBO Bill not mapped locally,
        pull it into the local cache via the existing sync_from_qbo flow
        and record an auto-fix issue. This catches records the delta-sync
        watermark may have skipped (e.g., during a deploy).
        """
        # Lazy imports to avoid pulling the QBO stack at module load.
        from integrations.intuit.qbo.bill.external.client import QboBillClient
        from integrations.intuit.qbo.bill.business.service import QboBillService
        from integrations.intuit.qbo.bill.connector.bill.business.service import (
            BillBillConnector,
        )
        from integrations.intuit.qbo.bill.connector.bill.persistence.repo import (
            BillBillRepository,
        )
        from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository

        # Auto-backfill gate. When off (default) we only COUNT the unprojected
        # backlog and emit a single low-severity summary — so a large backlog can't
        # be backfilled unintentionally and we don't write one high-severity issue
        # per bill per run (that flooded the table 600x/day). Flip to "true" to run
        # a controlled backfill.
        autofix_enabled = os.getenv("QBO_RECONCILE_BILL_AUTOFIX", "false").strip().lower() == "true"

        mapping_repo = BillBillRepository()
        qbo_bill_repo = QboBillRepository()
        qbo_bill_service = QboBillService()
        connector = BillBillConnector()

        auto_fixed = 0
        errors = 0
        missing = 0
        skipped_unmapped = 0

        with QboBillClient(realm_id=realm_id) as client:
            qbo_bills = client.query_all_bills()

        logger.info(
            f"Reconciliation fetched {len(qbo_bills)} bills from QBO for realm {realm_id} "
            f"(autofix_enabled={autofix_enabled})"
        )

        for qbo_bill in qbo_bills:
            try:
                # Is the QboBill already in our local cache with a Bill mapping?
                local_qbo_bill = qbo_bill_repo.read_by_qbo_id(qbo_bill.id)
                if local_qbo_bill:
                    mapping = mapping_repo.read_by_qbo_bill_id(local_qbo_bill.id)
                    if mapping:
                        # Fully synced — nothing to do.
                        continue

                # Missing locally (or staged but unmapped).
                missing += 1
                if not autofix_enabled:
                    # Backfill is deferred — count only, do not auto-create.
                    continue

                # Persist external → local dataclass first, then hand it to the connector.
                try:
                    local_bill, lines = qbo_bill_service.upsert_from_external(
                        qbo_bill, realm_id
                    )
                    connector.sync_from_qbo_bill(qbo_bill=local_bill, qbo_bill_lines=lines)
                    auto_fixed += 1
                    self._record_issue(
                        drift_type=DRIFT_QBO_MISSING_LOCALLY,
                        action="auto_fixed",
                        entity_type="Bill",
                        qbo_id=qbo_bill.id,
                        realm_id=realm_id,
                        details=f"Pulled QBO Bill {qbo_bill.id} into local cache via reconciliation.",
                        reconcile_run_id=run_id,
                    )
                except ValueError as data_error:
                    # Permanent data issue (e.g. vendor deleted/unmapped in QBO). It
                    # will never self-resolve, so skip quietly rather than re-flag a
                    # high-severity issue on every daily run.
                    skipped_unmapped += 1
                    logger.info(
                        f"Reconciliation skipped QBO Bill {qbo_bill.id} "
                        f"(unfixable data issue): {data_error}"
                    )
                except Exception as error:
                    errors += 1
                    logger.exception(
                        f"Reconciliation auto-fix failed for QBO Bill {qbo_bill.id}"
                    )
                    self._record_issue(
                        drift_type=DRIFT_QBO_MISSING_LOCALLY,
                        action="flagged",
                        severity_override="high",
                        entity_type="Bill",
                        qbo_id=qbo_bill.id,
                        realm_id=realm_id,
                        details=(
                            f"Auto-fix failed during reconciliation: {type(error).__name__}: {error}"
                        ),
                        reconcile_run_id=run_id,
                    )
            except Exception:
                errors += 1
                logger.exception(
                    f"Reconciliation error processing QBO Bill {getattr(qbo_bill, 'id', '?')}"
                )

        # One deduped low-severity summary instead of a per-bill flood when backfill
        # is deferred — keeps the backlog visible without spamming the issue table.
        if missing and not autofix_enabled:
            self._record_issue(
                drift_type=DRIFT_QBO_MISSING_LOCALLY,
                action="flagged",
                severity_override="low",
                entity_type="Bill",
                qbo_id=None,
                realm_id=realm_id,
                details=(
                    f"{missing} QBO Bill(s) are not projected locally. Auto-backfill is "
                    f"disabled (QBO_RECONCILE_BILL_AUTOFIX=false); set it true to backfill."
                ),
                reconcile_run_id=run_id,
            )

        return {
            "auto_fixed": auto_fixed,
            "missing": missing,
            "skipped_unmapped": skipped_unmapped,
            "flagged": errors,
            "errors": errors,
        }

    # ------------------------------------------------------------------ #
    # Void detection (task #21)
    # ------------------------------------------------------------------ #

    def _reconcile_bill_qbo_voided(self, realm_id: str, run_id: str) -> dict:
        """
        Detect QBO Bills that have been deleted/voided on the QBO side but
        still exist in our local cache.

        Strategy: for each locally-mapped QboBill, attempt to GET it from QBO
        by its QBO id. If QBO returns 404, the bill has been deleted/voided —
        flag an issue for operator review. We do NOT auto-delete the local
        record: that decision is semantic (should invoices referencing the
        bill be recomputed? did a user delete in error?) and deserves human
        judgment.

        This is O(N) in the count of locally-mapped bills with one QBO call
        each, so it's not cheap — scheduled daily at most. Batching via CDC
        would be more efficient but is deferred until call volume demands it.
        """
        from integrations.intuit.qbo.base.errors import QboNotFoundError
        from integrations.intuit.qbo.bill.external.client import QboBillClient
        from integrations.intuit.qbo.bill.connector.bill.persistence.repo import (
            BillBillRepository,
        )
        from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository

        mapping_repo = BillBillRepository()
        qbo_bill_repo = QboBillRepository()

        # Only check bills that have a local-bill mapping (i.e., were
        # successfully synced in both directions). Orphan QboBill rows with
        # no mapping are already flagged by the missing-locally detector.
        all_qbo_bills = qbo_bill_repo.read_by_realm_id(realm_id)

        flagged = 0
        errors = 0

        with QboBillClient(realm_id=realm_id) as client:
            for local_qbo_bill in all_qbo_bills:
                if not local_qbo_bill.qbo_id:
                    continue
                mapping = mapping_repo.read_by_qbo_bill_id(local_qbo_bill.id)
                if not mapping:
                    # Unmapped bills are the concern of the other detector.
                    continue

                try:
                    # Just fetch — we only care about 404 vs success.
                    client.get_bill(local_qbo_bill.qbo_id)
                except QboNotFoundError:
                    flagged += 1
                    self._record_issue(
                        drift_type=DRIFT_QBO_VOIDED,
                        action="flagged",
                        entity_type="Bill",
                        qbo_id=local_qbo_bill.qbo_id,
                        realm_id=realm_id,
                        details=(
                            f"QBO Bill {local_qbo_bill.qbo_id} is mapped locally "
                            f"(local QboBill id={local_qbo_bill.id}, mapped to "
                            f"Bill id={mapping.bill_id}) but returns 404 from QBO. "
                            f"Likely voided or deleted on the QBO side. Review "
                            f"before taking action — downstream invoices may "
                            f"reference this bill."
                        ),
                        reconcile_run_id=run_id,
                    )
                except Exception:
                    errors += 1
                    logger.exception(
                        f"qbo.reconcile.bill_qbo_voided.detector_error for "
                        f"qbo_id={local_qbo_bill.qbo_id}"
                    )

        return {"auto_fixed": 0, "flagged": flagged, "errors": errors}

    def _reconcile_purchase_qbo_missing_locally(self, realm_id: str, run_id: str) -> dict:
        """
        Full-scan QBO for all Purchases. For any QBO Purchase not mapped locally,
        pull it into the local cache via the existing sync_from_qbo flow
        and record an auto-fix issue.
        """
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
        from integrations.intuit.qbo.purchase.connector.expense.business.service import (
            PurchaseExpenseConnector,
        )
        from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import (
            PurchaseExpenseRepository,
        )
        from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

        autofix_enabled = os.getenv("QBO_RECONCILE_PURCHASE_AUTOFIX", "false").strip().lower() == "true"

        mapping_repo = PurchaseExpenseRepository()
        qbo_purchase_repo = QboPurchaseRepository()
        qbo_purchase_service = QboPurchaseService()
        connector = PurchaseExpenseConnector()

        auto_fixed = 0
        errors = 0
        missing = 0
        skipped_unmapped = 0

        with QboPurchaseClient(realm_id=realm_id) as client:
            qbo_purchases = client.query_all_purchases()

        logger.info(
            f"Reconciliation fetched {len(qbo_purchases)} purchases from QBO for realm {realm_id} "
            f"(autofix_enabled={autofix_enabled})"
        )

        for qbo_purchase in qbo_purchases:
            try:
                local = qbo_purchase_repo.read_by_qbo_id(qbo_purchase.id)
                if local:
                    mapping = mapping_repo.read_by_qbo_purchase_id(local.id)
                    if mapping:
                        continue

                missing += 1
                if not autofix_enabled:
                    continue

                try:
                    local_purchase, lines = qbo_purchase_service.upsert_from_external(
                        qbo_purchase, realm_id
                    )
                    connector.sync_from_qbo_purchase(
                        qbo_purchase=local_purchase, qbo_purchase_lines=lines
                    )
                    auto_fixed += 1
                    self._record_issue(
                        drift_type=DRIFT_QBO_MISSING_LOCALLY,
                        action="auto_fixed",
                        entity_type="Expense",
                        qbo_id=qbo_purchase.id,
                        realm_id=realm_id,
                        details=f"Pulled QBO Purchase {qbo_purchase.id} into local cache via reconciliation.",
                        reconcile_run_id=run_id,
                    )
                except ValueError as data_error:
                    skipped_unmapped += 1
                    logger.info(
                        f"Reconciliation skipped QBO Purchase {qbo_purchase.id} "
                        f"(unfixable data issue): {data_error}"
                    )
                except Exception as error:
                    errors += 1
                    logger.exception(
                        f"Reconciliation auto-fix failed for QBO Purchase {qbo_purchase.id}"
                    )
                    self._record_issue(
                        drift_type=DRIFT_QBO_MISSING_LOCALLY,
                        action="flagged",
                        severity_override="high",
                        entity_type="Expense",
                        qbo_id=qbo_purchase.id,
                        realm_id=realm_id,
                        details=(
                            f"Auto-fix failed during reconciliation: {type(error).__name__}: {error}"
                        ),
                        reconcile_run_id=run_id,
                    )
            except Exception:
                errors += 1
                logger.exception(
                    f"Reconciliation error processing QBO Purchase {getattr(qbo_purchase, 'id', '?')}"
                )

        if missing and not autofix_enabled:
            self._record_issue(
                drift_type=DRIFT_QBO_MISSING_LOCALLY,
                action="flagged",
                severity_override="low",
                entity_type="Expense",
                qbo_id=None,
                realm_id=realm_id,
                details=(
                    f"{missing} QBO Purchase(s) are not projected locally. Auto-backfill is "
                    f"disabled (QBO_RECONCILE_PURCHASE_AUTOFIX=false); set it true to backfill."
                ),
                reconcile_run_id=run_id,
            )

        return {
            "auto_fixed": auto_fixed,
            "missing": missing,
            "skipped_unmapped": skipped_unmapped,
            "flagged": errors,
            "errors": errors,
        }

    def _reconcile_purchase_qbo_voided(self, realm_id: str, run_id: str) -> dict:
        """
        Detect QBO Purchases that have been deleted/voided on the QBO side but
        still exist in our local cache.
        """
        from integrations.intuit.qbo.base.errors import QboNotFoundError
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
        from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import (
            PurchaseExpenseRepository,
        )
        from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

        mapping_repo = PurchaseExpenseRepository()
        qbo_purchase_repo = QboPurchaseRepository()

        all_qbo_purchases = qbo_purchase_repo.read_by_realm_id(realm_id)

        flagged = 0
        errors = 0

        with QboPurchaseClient(realm_id=realm_id) as client:
            for local in all_qbo_purchases:
                if not local.qbo_id:
                    continue
                mapping = mapping_repo.read_by_qbo_purchase_id(local.id)
                if not mapping:
                    continue

                try:
                    client.get_purchase(local.qbo_id)
                except QboNotFoundError:
                    flagged += 1
                    self._record_issue(
                        drift_type=DRIFT_QBO_VOIDED,
                        action="flagged",
                        entity_type="Expense",
                        qbo_id=local.qbo_id,
                        realm_id=realm_id,
                        details=(
                            f"QBO Purchase {local.qbo_id} is mapped locally "
                            f"(local QboPurchase id={local.id}, mapped to "
                            f"Expense id={mapping.expense_id}) but returns 404 from QBO. "
                            f"Likely voided or deleted on the QBO side. Review "
                            f"before taking action — downstream invoices may "
                            f"reference this expense."
                        ),
                        reconcile_run_id=run_id,
                    )
                except Exception:
                    errors += 1
                    logger.exception(
                        f"qbo.reconcile.purchase_qbo_voided.detector_error for "
                        f"qbo_id={local.qbo_id}"
                    )

        return {"auto_fixed": 0, "flagged": flagged, "errors": errors}

    def _reconcile_vendor_credit_qbo_missing_locally(self, realm_id: str, run_id: str) -> dict:
        """
        Full-scan QBO for all VendorCredits. For any QBO VendorCredit not mapped locally,
        pull it into the local cache via the existing sync_from_qbo flow
        and record an auto-fix issue.
        """
        from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
        from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
            VendorCreditBillCreditConnector,
        )
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import (
            VendorCreditBillCreditMappingRepository,
        )
        from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository

        autofix_enabled = os.getenv("QBO_RECONCILE_VENDORCREDIT_AUTOFIX", "false").strip().lower() == "true"

        mapping_repo = VendorCreditBillCreditMappingRepository()
        qbo_vc_repo = QboVendorCreditRepository()
        qbo_vc_service = QboVendorCreditService()
        connector = VendorCreditBillCreditConnector()

        auto_fixed = 0
        errors = 0
        missing = 0
        skipped_unmapped = 0

        with QboVendorCreditClient(realm_id=realm_id) as client:
            qbo_vcs = client.query_all_vendor_credits()

        logger.info(
            f"Reconciliation fetched {len(qbo_vcs)} vendor credits from QBO for realm {realm_id} "
            f"(autofix_enabled={autofix_enabled})"
        )

        for vc in qbo_vcs:
            try:
                local = qbo_vc_repo.read_by_qbo_id_and_realm_id(vc.id, realm_id)
                if local:
                    mapping = mapping_repo.read_by_qbo_vendor_credit_id(local.id)
                    if mapping:
                        continue

                missing += 1
                if not autofix_enabled:
                    continue

                try:
                    local_vc, lines = qbo_vc_service.upsert_from_external(vc, realm_id)
                    connector.sync_from_qbo_vendor_credit(local_vc, lines)
                    auto_fixed += 1
                    self._record_issue(
                        drift_type=DRIFT_QBO_MISSING_LOCALLY,
                        action="auto_fixed",
                        entity_type="BillCredit",
                        qbo_id=vc.id,
                        realm_id=realm_id,
                        details=f"Pulled QBO VendorCredit {vc.id} into local cache via reconciliation.",
                        reconcile_run_id=run_id,
                    )
                except ValueError as data_error:
                    skipped_unmapped += 1
                    logger.info(
                        f"Reconciliation skipped QBO VendorCredit {vc.id} "
                        f"(unfixable data issue): {data_error}"
                    )
                except Exception as error:
                    errors += 1
                    logger.exception(
                        f"Reconciliation auto-fix failed for QBO VendorCredit {vc.id}"
                    )
                    self._record_issue(
                        drift_type=DRIFT_QBO_MISSING_LOCALLY,
                        action="flagged",
                        severity_override="high",
                        entity_type="BillCredit",
                        qbo_id=vc.id,
                        realm_id=realm_id,
                        details=(
                            f"Auto-fix failed during reconciliation: {type(error).__name__}: {error}"
                        ),
                        reconcile_run_id=run_id,
                    )
            except Exception:
                errors += 1
                logger.exception(
                    f"Reconciliation error processing QBO VendorCredit {getattr(vc, 'id', '?')}"
                )

        if missing and not autofix_enabled:
            self._record_issue(
                drift_type=DRIFT_QBO_MISSING_LOCALLY,
                action="flagged",
                severity_override="low",
                entity_type="BillCredit",
                qbo_id=None,
                realm_id=realm_id,
                details=(
                    f"{missing} QBO VendorCredit(s) are not projected locally. Auto-backfill is "
                    f"disabled (QBO_RECONCILE_VENDORCREDIT_AUTOFIX=false); set it true to backfill."
                ),
                reconcile_run_id=run_id,
            )

        return {
            "auto_fixed": auto_fixed,
            "missing": missing,
            "skipped_unmapped": skipped_unmapped,
            "flagged": errors,
            "errors": errors,
        }

    def _reconcile_vendor_credit_qbo_voided(self, realm_id: str, run_id: str) -> dict:
        """
        Detect QBO VendorCredits that have been deleted/voided on the QBO side but
        still exist in our local cache.
        """
        from integrations.intuit.qbo.base.errors import QboNotFoundError
        from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import (
            VendorCreditBillCreditMappingRepository,
        )
        from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository

        mapping_repo = VendorCreditBillCreditMappingRepository()
        qbo_vc_repo = QboVendorCreditRepository()

        all_qbo_vcs = qbo_vc_repo.read_by_realm_id(realm_id)

        flagged = 0
        errors = 0

        with QboVendorCreditClient(realm_id=realm_id) as client:
            for local in all_qbo_vcs:
                if not local.qbo_id:
                    continue
                mapping = mapping_repo.read_by_qbo_vendor_credit_id(local.id)
                if not mapping:
                    continue

                try:
                    client.get_vendor_credit(local.qbo_id)
                except QboNotFoundError:
                    flagged += 1
                    self._record_issue(
                        drift_type=DRIFT_QBO_VOIDED,
                        action="flagged",
                        entity_type="BillCredit",
                        qbo_id=local.qbo_id,
                        realm_id=realm_id,
                        details=(
                            f"QBO VendorCredit {local.qbo_id} is mapped locally "
                            f"(local QboVendorCredit id={local.id}, mapped to "
                            f"BillCredit id={mapping.bill_credit_id}) but returns 404 from QBO. "
                            f"Likely voided or deleted on the QBO side. Review "
                            f"before taking action — downstream invoices may "
                            f"reference this bill credit."
                        ),
                        reconcile_run_id=run_id,
                    )
                except Exception:
                    errors += 1
                    logger.exception(
                        f"qbo.reconcile.vendor_credit_qbo_voided.detector_error for "
                        f"qbo_id={local.qbo_id}"
                    )

        return {"auto_fixed": 0, "flagged": flagged, "errors": errors}

    # ------------------------------------------------------------------ #
    # Issue-recording helper (shared across all detectors)
    # ------------------------------------------------------------------ #

    def _record_issue(
        self,
        *,
        drift_type: str,
        action: str,
        entity_type: str,
        realm_id: str,
        entity_public_id: Optional[str] = None,
        qbo_id: Optional[str] = None,
        details: Optional[str] = None,
        reconcile_run_id: Optional[str] = None,
        severity_override: Optional[str] = None,
    ) -> None:
        severity = severity_override or SEVERITY_BY_DRIFT.get(drift_type, "medium")
        try:
            self.repo.create(
                drift_type=drift_type,
                severity=severity,
                action=action,
                entity_type=entity_type,
                entity_public_id=entity_public_id,
                qbo_id=qbo_id,
                realm_id=realm_id,
                details=details,
                reconcile_run_id=reconcile_run_id,
            )
            log_event = (
                "qbo.reconcile.auto_fix.applied" if action == "auto_fixed"
                else "qbo.reconcile.issue.flagged"
            )
            logger.info(
                log_event,
                extra={
                    "event_name": log_event,
                    "drift_type": drift_type,
                    "severity": severity,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "qbo_id": qbo_id,
                    "realm_id": realm_id,
                    "reconcile_run_id": reconcile_run_id,
                },
            )
        except Exception:
            # Recording the issue is best-effort; failing to record should
            # not crash the entire reconciliation run.
            logger.exception(
                f"Failed to record reconciliation issue "
                f"(drift={drift_type}, entity={entity_type}, qbo_id={qbo_id})"
            )
