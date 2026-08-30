# Python Standard Library Imports
import logging
import os
import uuid
from decimal import Decimal
from typing import Optional

# Local Imports
from shared.authz.context import current_is_system_admin, current_user_id, system_authz
from integrations.intuit.qbo.base.drift_types import (
    DRIFT_BILLABLE_STATUS_DRIFT,
    DRIFT_DUPLICATE_MAPPING,
    DRIFT_FIELD_MISMATCH,
    DRIFT_INVOICE_DRAW_MISMATCH,
    DRIFT_LOCAL_MISSING_QBO,
    DRIFT_MISSING_MAPPING,
    DRIFT_QBO_MISSING_LOCALLY,
    DRIFT_QBO_VOIDED,
    DRIFT_STALE_SYNC_TOKEN,
)
from integrations.intuit.qbo.base.delete_reconcile import DEFAULT_VOID_MAX_CANDIDATES
from integrations.intuit.qbo.reconciliation.persistence.repo import (
    ReconciliationIssueRepository,
)

logger = logging.getLogger(__name__)


# Drift-type severity policy for the tiered auto-fix/flag reconciler (Chapter 5).
# DriftType string constants live in integrations.intuit.qbo.base.drift_types.
#
# - low   → auto-fixable; service applies the fix and writes the issue for audit.
# - medium → flagged; operator reviews and decides.
# - high   → flagged; human judgment required (never auto-fix).
SEVERITY_BY_DRIFT = {
    DRIFT_QBO_MISSING_LOCALLY: "low",
    DRIFT_LOCAL_MISSING_QBO: "medium",
    DRIFT_STALE_SYNC_TOKEN: "low",
    DRIFT_MISSING_MAPPING: "low",
    DRIFT_FIELD_MISMATCH: "medium",
    DRIFT_DUPLICATE_MAPPING: "high",
    DRIFT_QBO_VOIDED: "low",
    DRIFT_INVOICE_DRAW_MISMATCH: "medium",
    DRIFT_BILLABLE_STATUS_DRIFT: "medium",
}

# Counter keys rolled up from each detector into a reconcile run summary.
# flagged_deduped is a SUBSET of flagged: a re-seen void still counts as
# flagged (it was really detected + 404-confirmed); only its duplicate
# issue-write is suppressed. Do not add them together.
RECONCILE_COUNT_KEYS = ("auto_fixed", "flagged", "flagged_deduped", "errors")


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
        # Per-run dedupe-key cache, keyed by drift_type (U-335 generalized this
        # from a single qbo_voided-only cache once a second drift_type needed
        # the identical idiom — see _unresolved_keys). Scoped to ONE reconcile
        # run: ReconciliationService is constructed per-invocation (admin
        # reconcile router, scheduler _sync_reconcile_bills), so every detector
        # sharing a drift_type (the qbo_voided trio; the billable_status_drift
        # Bill/Purchase pair) shares one fetch per drift_type and the cache dies
        # with the run. If this service ever becomes long-lived or a singleton,
        # invalidate this cache per run — otherwise it goes stale against issues
        # resolved in SQL mid-life.
        self._dedupe_key_caches: dict = {}
        # U-301a: same per-run memoization idiom as _void_key_cache, for
        # dbo.Expense's (Id, QboId) identity rows — reconcile_purchases's two
        # detectors both need this realm-scoped read; without caching it here
        # each would fetch it independently. Keyed by realm_id (not just a
        # single scalar) for defensive correctness if a future caller ever
        # reconciles more than one realm on one instance.
        self._expense_identity_rows_cache: dict = {}
        # U-305: same idiom for dbo.Bill's and dbo.BillCredit's (Id, QboId)
        # identity rows (Bill/VendorCredit fan-out of U-301a's Expense pilot).
        self._bill_identity_rows_cache: dict = {}
        self._vendor_credit_identity_rows_cache: dict = {}

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

        U-305: wrapped in system_authz() — both detectors now resolve identity
        via RBAC-gated dbo.Bill bulk reads (identity_drift.py's registry-driven
        read_qbo_identity_rows_by_realm_id), unlike the unguarded qbo.* staging
        reads they replaced. Both real callers (shared/api/admin.py's
        drain-secret route, shared/scheduler.py's dormant fallback) already
        establish system-admin context before calling this, so this is
        belt-and-suspenders — same pattern reconcile_purchases adopted in
        U-301a.

        Returns a summary dict suitable for structured logging.
        """
        with system_authz():
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

            counts = dict.fromkeys(RECONCILE_COUNT_KEYS, 0)

            # Detector 1: QBO-missing-locally
            try:
                d1 = self._reconcile_bill_qbo_missing_locally(
                    realm_id=realm_id, run_id=run_id
                )
                for key in RECONCILE_COUNT_KEYS:
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
                for key in RECONCILE_COUNT_KEYS:
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
                    **counts,
                },
            )
            return {"run_id": run_id, **counts}

    def reconcile_purchases(self, realm_id: str) -> dict:
        """
        Full-scan reconciliation for Purchases (Expenses).

        Detectors run in this order:
          1. qbo_missing_locally — auto-fix (pull)
          2. qbo_voided — flag local Expenses whose QBO counterpart no longer exists

        U-301a: wrapped in system_authz() — both detectors now resolve identity
        via RBAC-gated dbo.Expense bulk reads (ExpenseService), unlike the
        unguarded qbo.* staging reads they replaced. Both real callers
        (shared/api/admin.py's drain-secret route, shared/scheduler.py's
        dormant fallback) already establish system-admin context before
        calling this, so this is belt-and-suspenders — same self-declaration
        pattern the outbox workers use (shared/authz/context.py::system_authz)
        against a future entry point that forgets to.
        """
        with system_authz():
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

            counts = dict.fromkeys(RECONCILE_COUNT_KEYS, 0)

            try:
                d1 = self._reconcile_purchase_qbo_missing_locally(
                    realm_id=realm_id, run_id=run_id
                )
                for key in RECONCILE_COUNT_KEYS:
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
                for key in RECONCILE_COUNT_KEYS:
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
                    **counts,
                },
            )
            return {"run_id": run_id, **counts}

    def reconcile_vendor_credits(self, realm_id: str) -> dict:
        """
        Full-scan reconciliation for VendorCredits (BillCredits).

        Detectors run in this order:
          1. qbo_missing_locally — auto-fix (pull)
          2. qbo_voided — flag local BillCredits whose QBO counterpart no longer exists

        U-305: wrapped in system_authz() — same rationale as reconcile_bills
        above (both detectors now resolve identity via RBAC-gated dbo.BillCredit
        bulk reads, belt-and-suspenders against a future caller omitting
        system-admin context).
        """
        with system_authz():
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

            counts = dict.fromkeys(RECONCILE_COUNT_KEYS, 0)

            try:
                d1 = self._reconcile_vendor_credit_qbo_missing_locally(
                    realm_id=realm_id, run_id=run_id
                )
                for key in RECONCILE_COUNT_KEYS:
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
                for key in RECONCILE_COUNT_KEYS:
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
                    **counts,
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

    def reconcile_billable_status_drift(self, realm_id: str) -> dict:
        """
        Cross-source-state drift detector (U-335): flag BillLineItem /
        ExpenseLineItem rows that are locally IsBilled=1 but whose mapped QBO
        line still carries BillableStatus='Billable'. We deliberately never
        push BillableStatus back to QBO on invoice completion
        (entities/invoice/business/service.py::_sync_billed_status_to_qbo is
        defined but has zero call sites — complete_invoice never calls it), so
        QBO's Suggested Transactions tray keeps re-suggesting lines that are
        already billed locally.

        Pure SQL, no QBO API calls — same shape as reconcile_invoice_draws.
        Detectors run in this order:
          1. Bill branch — dbo.BillLineItem -> qbo.BillLineItemBillLine -> qbo.BillLine
          2. Purchase branch — dbo.ExpenseLineItem -> qbo.PurchaseLineExpenseLineItem -> qbo.PurchaseLine

        FLAG-ONLY: never auto-fixes, never mutates BillableStatus, never
        touches the source rows. Aggregates to ONE issue per drifting parent
        (Bill / Expense) — not per line — sized 2026-08-30 at ~1,104 drifting
        lines / ~600 parents / ~$5.35M live; a per-line issue would flood the
        table. Idempotent via the same (realm_id, entity_type, qbo_id)
        unresolved-key dedupe the qbo_voided detectors use (see
        _unresolved_keys), so a daily re-run only writes NEWLY-drifting
        parents.
        """
        run_id = str(uuid.uuid4())
        logger.info(
            "qbo.reconcile.run.started",
            extra={
                "event_name": "qbo.reconcile.run.started",
                "operation_name": "qbo.reconcile.billable_status_drift",
                "entity_type": "BillLineItem/ExpenseLineItem",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
            },
        )

        counts = dict.fromkeys(RECONCILE_COUNT_KEYS, 0)

        try:
            d1 = self._reconcile_bill_billable_status_drift(realm_id=realm_id, run_id=run_id)
            for key in RECONCILE_COUNT_KEYS:
                counts[key] += d1.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "bill_billable_status_drift",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        try:
            d2 = self._reconcile_purchase_billable_status_drift(realm_id=realm_id, run_id=run_id)
            for key in RECONCILE_COUNT_KEYS:
                counts[key] += d2.get(key, 0)
        except Exception:
            logger.exception("qbo.reconcile.detector.failed",
                             extra={"detector": "purchase_billable_status_drift",
                                    "reconcile_run_id": run_id})
            counts["errors"] += 1

        logger.info(
            "qbo.reconcile.run.completed",
            extra={
                "event_name": "qbo.reconcile.run.completed",
                "operation_name": "qbo.reconcile.billable_status_drift",
                "entity_type": "BillLineItem/ExpenseLineItem",
                "realm_id": realm_id,
                "reconcile_run_id": run_id,
                **counts,
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

        U-305: "already synced" is resolved via dbo.Bill's own native QboId
        (U-238a), loaded once per run (shared with the voided detector via
        _bill_identity_rows, mirroring _unresolved_keys's per-run
        memoization) instead of a per-record qbo.Bill + qbo.BillBill
        staging/mapping round trip — Bill/VendorCredit fan-out of U-301a's
        Expense pilot.
        """
        # Lazy imports to avoid pulling the QBO stack at module load.
        from integrations.intuit.qbo.bill.external.client import QboBillClient
        from integrations.intuit.qbo.bill.business.service import QboBillService
        from integrations.intuit.qbo.bill.connector.bill.business.service import (
            BillBillConnector,
        )

        # Auto-backfill gate. When off (default) we only COUNT the unprojected
        # backlog and emit a single low-severity summary — so a large backlog can't
        # be backfilled unintentionally and we don't write one high-severity issue
        # per bill per run (that flooded the table 600x/day). Flip to "true" to run
        # a controlled backfill.
        autofix_enabled = os.getenv("QBO_RECONCILE_BILL_AUTOFIX", "false").strip().lower() == "true"

        qbo_bill_service = QboBillService()
        connector = BillBillConnector()

        auto_fixed = 0
        errors = 0
        missing = 0
        skipped_unmapped = 0

        with QboBillClient(realm_id=realm_id) as client:
            qbo_bills = client.query_all_bills()

        # U-305: this bulk read replaces what used to be a per-record identity
        # lookup inside the loop's own try/except below — a failure here can no
        # longer be isolated to one bill (there is nothing left to check
        # per-record against), so it is caught explicitly and attributed loudly
        # instead of falling through to the generic per-detector catch-all in
        # reconcile_bills, which would otherwise mask exactly what failed.
        try:
            dbo_qbo_ids = {row.qbo_id for row in self._bill_identity_rows(realm_id)}
        except Exception:
            logger.exception(
                "qbo.reconcile.bill_qbo_missing_locally.identity_read_failed",
                extra={
                    "event_name": "qbo.reconcile.bill_qbo_missing_locally.identity_read_failed",
                    "realm_id": realm_id,
                    "reconcile_run_id": run_id,
                },
            )
            return {"auto_fixed": 0, "missing": 0, "skipped_unmapped": 0, "flagged": 1, "errors": 1}

        logger.info(
            f"Reconciliation fetched {len(qbo_bills)} bills from QBO for realm {realm_id} "
            f"(autofix_enabled={autofix_enabled})"
        )

        for qbo_bill in qbo_bills:
            try:
                if qbo_bill.id in dbo_qbo_ids:
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

    def _unresolved_keys(self, drift_type: str) -> set:
        """Open (realm_id, entity_type, qbo_id) dedupe keys for `drift_type`,
        cached per drift_type for the life of this ReconciliationService
        instance (see _dedupe_key_caches) — generalized (U-335) from a
        qbo_voided-only cache once billable_status_drift needed the identical
        idiom (same shape as _identity_rows_for's generalization below).

        Returns the live per-run cache set (not a copy); callers may .add() a
        key they have just durably written, and that key is then visible to
        other detectors sharing this drift_type in the same run — safe
        because entity_type is part of the key.
        """
        if drift_type in self._dedupe_key_caches:
            return self._dedupe_key_caches[drift_type]
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        try:
            rows = self.repo.read_unresolved_issue_keys_by_drift_type(drift_type)
            keys = set()
            for realm_id, entity_type, qbo_id in rows:
                normalized = normalize_qbo_id(qbo_id)
                if not normalized:
                    continue
                keys.add((realm_id, entity_type, normalized))
        except Exception:
            # Fail open: empty key set means every drifting/voided candidate
            # writes its issue — exactly pre-U-160 behaviour. Suppression must
            # NEVER be the failure mode: a duplicate row is cheap, a lost flag
            # is not.
            logger.exception(
                "qbo.reconcile.dedupe.key_fetch_failed",
                extra={"event_name": "qbo.reconcile.dedupe.key_fetch_failed", "drift_type": drift_type},
            )
            keys = set()
        self._dedupe_key_caches[drift_type] = keys
        return self._dedupe_key_caches[drift_type]

    def _expense_identity_rows(self, realm_id: str) -> list:
        """dbo.Expense's (Id, QboId) identity rows for a realm (U-301a), cached
        for the life of this ReconciliationService instance — see
        _expense_identity_rows_cache. reconcile_purchases's two detectors both
        need this same realm-scoped read; without this, each would fetch it
        independently. Raises on failure — callers decide how to handle it
        (the missing-locally detector returns a degraded result; the voided
        detector already had no local guard around its equivalent bulk read
        pre-U-301a and is unaffected).
        """
        if realm_id in self._expense_identity_rows_cache:
            return self._expense_identity_rows_cache[realm_id]
        from entities.expense.business.service import ExpenseService

        rows = ExpenseService().read_qbo_identity_rows_by_realm_id(realm_id)
        self._expense_identity_rows_cache[realm_id] = rows
        return rows

    def _identity_rows_for(self, *, cache: dict, specs, key: str, realm_id: str) -> list:
        """Shared body for _bill_identity_rows / _vendor_credit_identity_rows
        below (U-305) — both differ only in which registry (HEADER_ENTITY_SPECS
        vs REFERENCE_ENTITY_SPECS), spec key, and per-run cache dict they pass.
        Cached for the life of this ReconciliationService instance so each
        family's two detectors (missing-locally, voided) share one fetch
        instead of independently re-fetching the same realm-scoped set.
        Raises on failure — callers decide how to handle it (the
        missing-locally detectors return a degraded result; the voided
        detectors have no local guard, same as the pre-U-305 equivalents).

        Sourced from identity_drift.py's registry-driven bulk read
        (Decision-1, U-305) rather than two hand-copied entity-specific
        sprocs — one generic function backs both families.
        """
        if realm_id in cache:
            return cache[realm_id]
        from integrations.intuit.qbo.base.identity_drift import read_qbo_identity_rows_by_realm_id

        spec = next(s for s in specs if s.key == key)
        rows = read_qbo_identity_rows_by_realm_id(
            spec,
            realm_id,
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )
        cache[realm_id] = rows
        return rows

    def _bill_identity_rows(self, realm_id: str) -> list:
        """dbo.Bill's (Id, QboId) identity rows for a realm (U-305) — see
        _identity_rows_for. Bill/VendorCredit fan-out of U-301a's Expense
        pilot; reconcile_bills's two detectors share this one fetch.
        """
        from integrations.intuit.qbo.base.identity_drift import HEADER_ENTITY_SPECS

        return self._identity_rows_for(
            cache=self._bill_identity_rows_cache,
            specs=HEADER_ENTITY_SPECS,
            key="bill",
            realm_id=realm_id,
        )

    def _vendor_credit_identity_rows(self, realm_id: str) -> list:
        """dbo.BillCredit's (Id, QboId) identity rows for a realm (U-305) —
        see _identity_rows_for. Mirrors _bill_identity_rows above;
        reconcile_vendor_credits's two detectors share this one fetch.
        """
        from integrations.intuit.qbo.base.identity_drift import REFERENCE_ENTITY_SPECS

        return self._identity_rows_for(
            cache=self._vendor_credit_identity_rows_cache,
            specs=REFERENCE_ENTITY_SPECS,
            key="bill_credit",
            realm_id=realm_id,
        )

    def _reconcile_bill_qbo_voided(self, realm_id: str, run_id: str) -> dict:
        """
        Detect QBO Bills that have been deleted/voided on the QBO side but
        still exist in our local cache.

        Strategy: page the live id list once, diff against local mappings,
        confirm each candidate with a single GET. Coverage equals the prior
        per-record scan for any run at or below the candidate ceiling: only
        hard-deleted records 404; QBO-voided-but-present records return 200 and
        appear in the query, so neither the old scan nor this one flags them.
        ABOVE the ceiling the detector deliberately trades coverage for safety
        — it flags nothing and records one summary issue, because a candidate
        set that large is far more likely to be a bad id fetch than a real mass
        deletion.

        We do NOT auto-delete the local record: that decision is semantic
        (should invoices referencing the bill be recomputed? did a user delete
        in error?) and deserves human judgment.

        U-305: local_rows is now dbo.Bill's own (Id, QboId) identity rows
        (U-238a) instead of qbo.Bill staging rows, and lookup_mapping is
        trivial (the row itself) — a dbo.Bill row only appears here because
        it already carries a QboId, so "is this mapped" is true by
        construction, with no separate qbo.BillBill mapping-table read
        needed. Shares the missing-locally detector's cached read
        (_bill_identity_rows) rather than re-fetching the same realm-scoped
        set independently.
        """
        from integrations.intuit.qbo.base.delete_reconcile import (
            detect_void_absent_candidates,
        )
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        from integrations.intuit.qbo.bill.external.client import QboBillClient

        all_dbo_bills = self._bill_identity_rows(realm_id)

        # Nothing mapped locally means there is no diff to compute, so skip the
        # id fetch entirely — saving API calls is the whole point of this detector.
        # Deliberate delta: on an empty local set the old path would still fetch and
        # surface an id-fetch failure as errors=1. There is nothing to flag either
        # way, so a no-op run reports clean rather than burning ~20 calls to fail.
        if not all_dbo_bills:
            return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}

        flagged = 0
        flagged_deduped = 0
        errors = 0

        with QboBillClient(realm_id=realm_id) as client:
            diff = detect_void_absent_candidates(
                local_rows=all_dbo_bills,
                realm_id=realm_id,
                reconcile_run_id=run_id,
                log_prefix="qbo.reconcile.bill_qbo_voided",
                fetch_live_ids=client.query_all_bill_ids,
                confirm_get=client.get_bill,
                extract_qbo_id=lambda row: normalize_qbo_id(row.qbo_id),
                lookup_mapping=lambda row: row,
            )

            if diff.aborted and diff.abort_reason == "ceiling_exceeded":
                max_candidates = diff.ceiling
                self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    severity_override="high",
                    entity_type="Bill",
                    qbo_id=None,
                    realm_id=realm_id,
                    details=(
                        f"Void detection aborted: {diff.candidate_count} locally-mapped Bill(s) were absent "
                        f"from the QBO id query, above the {max_candidates} candidate ceiling "
                        f"({diff.mapped_count} mapped, {diff.live_count} live). Nothing was flagged - "
                        f"this is far more likely an incomplete id fetch than a mass deletion. "
                        f"Raise QBO_RECONCILE_VOID_MAX_CANDIDATES only after confirming in QBO."
                    ),
                    reconcile_run_id=run_id,
                )
                return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 1}

            errors += diff.errors

            for candidate in diff.confirmed_voids:
                local = candidate.local_row
                qbo_id = candidate.qbo_id
                flagged += 1
                key = (realm_id, "Bill", qbo_id)
                # fetched lazily on the first confirmed 404 — a run with no voids pays no
                # query; the instance cache keeps it to one fetch per run.
                void_keys = self._unresolved_keys(DRIFT_QBO_VOIDED)
                if key in void_keys:
                    flagged_deduped += 1
                    logger.info(
                        "qbo.reconcile.bill_qbo_voided.void_issue_deduped",
                        extra={
                            "event_name": "qbo.reconcile.bill_qbo_voided.void_issue_deduped",
                            "qbo_id": qbo_id,
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )
                    continue
                # A failed write must NOT suppress a later retry — suppression is only
                # ever justified by a row that really exists.
                if self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    entity_type="Bill",
                    qbo_id=qbo_id,
                    realm_id=realm_id,
                    details=(
                        f"QBO Bill {qbo_id} is mapped locally "
                        f"(Bill id={local.id}) but returns 404 from QBO. "
                        f"Likely voided or deleted on the QBO side. Review "
                        f"before taking action — downstream invoices may "
                        f"reference this bill."
                    ),
                    reconcile_run_id=run_id,
                ):
                    void_keys.add(key)

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": errors}

    def _reconcile_purchase_qbo_missing_locally(self, realm_id: str, run_id: str) -> dict:
        """
        Full-scan QBO for all Purchases. For any QBO Purchase not mapped locally,
        pull it into the local cache via the existing sync_from_qbo flow
        and record an auto-fix issue.

        U-301a: "already synced" is resolved via dbo.Expense's own native QboId
        (U-238a), loaded once per run (shared with the voided detector via
        _expense_identity_rows, mirroring _unresolved_keys's per-run
        memoization) instead of a per-record qbo.Purchase + qbo.PurchaseExpense
        staging/mapping round trip — same identity PurchaseExpenseConnector's
        fast path already resolves by.
        """
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
        from integrations.intuit.qbo.purchase.connector.expense.business.service import (
            PurchaseExpenseConnector,
        )

        autofix_enabled = os.getenv("QBO_RECONCILE_PURCHASE_AUTOFIX", "false").strip().lower() == "true"

        qbo_purchase_service = QboPurchaseService()
        connector = PurchaseExpenseConnector()

        auto_fixed = 0
        errors = 0
        missing = 0
        skipped_unmapped = 0

        with QboPurchaseClient(realm_id=realm_id) as client:
            qbo_purchases = client.query_all_purchases()

        # U-301a: this bulk read replaces what used to be a per-record identity
        # lookup inside the loop's own try/except below — a failure here can no
        # longer be isolated to one purchase (there is nothing left to check
        # per-record against), so it is caught explicitly and attributed loudly
        # instead of falling through to the generic per-detector catch-all in
        # reconcile_purchases, which would otherwise mask exactly what failed.
        try:
            dbo_qbo_ids = {row.qbo_id for row in self._expense_identity_rows(realm_id)}
        except Exception:
            logger.exception(
                "qbo.reconcile.purchase_qbo_missing_locally.identity_read_failed",
                extra={
                    "event_name": "qbo.reconcile.purchase_qbo_missing_locally.identity_read_failed",
                    "realm_id": realm_id,
                    "reconcile_run_id": run_id,
                },
            )
            return {"auto_fixed": 0, "missing": 0, "skipped_unmapped": 0, "flagged": 1, "errors": 1}

        logger.info(
            f"Reconciliation fetched {len(qbo_purchases)} purchases from QBO for realm {realm_id} "
            f"(autofix_enabled={autofix_enabled})"
        )

        for qbo_purchase in qbo_purchases:
            try:
                if qbo_purchase.id in dbo_qbo_ids:
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

        Strategy: page the live id list once, diff against local mappings,
        confirm each candidate with a single GET. Coverage equals the prior
        per-record scan for any run at or below the candidate ceiling: only
        hard-deleted records 404; QBO-voided-but-present records return 200 and
        appear in the query, so neither the old scan nor this one flags them.
        ABOVE the ceiling the detector deliberately trades coverage for safety
        — it flags nothing and records one summary issue, because a candidate
        set that large is far more likely to be a bad id fetch than a real mass
        deletion.

        U-301a: local_rows is now dbo.Expense's own (Id, QboId) identity rows
        (U-238a) instead of qbo.Purchase staging rows, and lookup_mapping is
        trivial (the row itself) — a dbo.Expense row only appears here because
        it already carries a QboId, so "is this mapped" is true by construction,
        with no separate qbo.PurchaseExpense mapping-table read needed. Shares
        the missing-locally detector's cached read (_expense_identity_rows)
        rather than re-fetching the same realm-scoped set independently.
        """
        from integrations.intuit.qbo.base.delete_reconcile import (
            detect_void_absent_candidates,
        )
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient

        all_dbo_expenses = self._expense_identity_rows(realm_id)

        # Nothing mapped locally means there is no diff to compute, so skip the
        # id fetch entirely — saving API calls is the whole point of this detector.
        # Deliberate delta: on an empty local set the old path would still fetch and
        # surface an id-fetch failure as errors=1. There is nothing to flag either
        # way, so a no-op run reports clean rather than burning ~20 calls to fail.
        if not all_dbo_expenses:
            return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}

        flagged = 0
        flagged_deduped = 0
        errors = 0

        with QboPurchaseClient(realm_id=realm_id) as client:
            diff = detect_void_absent_candidates(
                local_rows=all_dbo_expenses,
                realm_id=realm_id,
                reconcile_run_id=run_id,
                log_prefix="qbo.reconcile.purchase_qbo_voided",
                fetch_live_ids=client.query_all_purchase_ids,
                confirm_get=client.get_purchase,
                extract_qbo_id=lambda row: normalize_qbo_id(row.qbo_id),
                lookup_mapping=lambda row: row,
            )

            if diff.aborted and diff.abort_reason == "ceiling_exceeded":
                max_candidates = diff.ceiling
                self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    severity_override="high",
                    entity_type="Expense",
                    qbo_id=None,
                    realm_id=realm_id,
                    details=(
                        f"Void detection aborted: {diff.candidate_count} locally-mapped Expense(s) were absent "
                        f"from the QBO id query, above the {max_candidates} candidate ceiling "
                        f"({diff.mapped_count} mapped, {diff.live_count} live). Nothing was flagged - "
                        f"this is far more likely an incomplete id fetch than a mass deletion. "
                        f"Raise QBO_RECONCILE_VOID_MAX_CANDIDATES only after confirming in QBO."
                    ),
                    reconcile_run_id=run_id,
                )
                return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 1}

            errors += diff.errors

            for candidate in diff.confirmed_voids:
                local = candidate.local_row
                qbo_id = candidate.qbo_id
                flagged += 1
                key = (realm_id, "Expense", qbo_id)
                # fetched lazily on the first confirmed 404 — a run with no voids pays no
                # query; the instance cache keeps it to one fetch per run.
                void_keys = self._unresolved_keys(DRIFT_QBO_VOIDED)
                if key in void_keys:
                    flagged_deduped += 1
                    logger.info(
                        "qbo.reconcile.purchase_qbo_voided.void_issue_deduped",
                        extra={
                            "event_name": "qbo.reconcile.purchase_qbo_voided.void_issue_deduped",
                            "qbo_id": qbo_id,
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )
                    continue
                # A failed write must NOT suppress a later retry — suppression is only
                # ever justified by a row that really exists.
                if self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    entity_type="Expense",
                    qbo_id=qbo_id,
                    realm_id=realm_id,
                    details=(
                        f"QBO Purchase {qbo_id} is mapped locally "
                        f"(Expense id={local.id}) but returns 404 from QBO. "
                        f"Likely voided or deleted on the QBO side. Review "
                        f"before taking action — downstream invoices may "
                        f"reference this expense."
                    ),
                    reconcile_run_id=run_id,
                ):
                    void_keys.add(key)

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": errors}

    def _reconcile_vendor_credit_qbo_missing_locally(self, realm_id: str, run_id: str) -> dict:
        """
        Full-scan QBO for all VendorCredits. For any QBO VendorCredit not mapped locally,
        pull it into the local cache via the existing sync_from_qbo flow
        and record an auto-fix issue.

        U-305: "already synced" is resolved via dbo.BillCredit's own native
        QboId (U-238a), loaded once per run (shared with the voided detector
        via _vendor_credit_identity_rows, mirroring _unresolved_keys's
        per-run memoization) instead of a per-record qbo.VendorCredit +
        qbo.VendorCreditBillCredit staging/mapping round trip.
        """
        from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
        from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
            VendorCreditBillCreditConnector,
        )

        autofix_enabled = os.getenv("QBO_RECONCILE_VENDORCREDIT_AUTOFIX", "false").strip().lower() == "true"

        qbo_vc_service = QboVendorCreditService()
        connector = VendorCreditBillCreditConnector()

        auto_fixed = 0
        errors = 0
        missing = 0
        skipped_unmapped = 0

        with QboVendorCreditClient(realm_id=realm_id) as client:
            qbo_vcs = client.query_all_vendor_credits()

        # U-305: this bulk read replaces what used to be a per-record identity
        # lookup inside the loop's own try/except below — a failure here can no
        # longer be isolated to one vendor credit (there is nothing left to check
        # per-record against), so it is caught explicitly and attributed loudly
        # instead of falling through to the generic per-detector catch-all in
        # reconcile_vendor_credits, which would otherwise mask exactly what failed.
        try:
            dbo_qbo_ids = {row.qbo_id for row in self._vendor_credit_identity_rows(realm_id)}
        except Exception:
            logger.exception(
                "qbo.reconcile.vendor_credit_qbo_missing_locally.identity_read_failed",
                extra={
                    "event_name": "qbo.reconcile.vendor_credit_qbo_missing_locally.identity_read_failed",
                    "realm_id": realm_id,
                    "reconcile_run_id": run_id,
                },
            )
            return {"auto_fixed": 0, "missing": 0, "skipped_unmapped": 0, "flagged": 1, "errors": 1}

        logger.info(
            f"Reconciliation fetched {len(qbo_vcs)} vendor credits from QBO for realm {realm_id} "
            f"(autofix_enabled={autofix_enabled})"
        )

        for vc in qbo_vcs:
            try:
                if vc.id in dbo_qbo_ids:
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

        Strategy: page the live id list once, diff against local mappings,
        confirm each candidate with a single GET. Coverage equals the prior
        per-record scan for any run at or below the candidate ceiling: only
        hard-deleted records 404; QBO-voided-but-present records return 200 and
        appear in the query, so neither the old scan nor this one flags them.
        ABOVE the ceiling the detector deliberately trades coverage for safety
        — it flags nothing and records one summary issue, because a candidate
        set that large is far more likely to be a bad id fetch than a real mass
        deletion.

        U-305: local_rows is now dbo.BillCredit's own (Id, QboId) identity
        rows (U-238a) instead of qbo.VendorCredit staging rows, and
        lookup_mapping is trivial (the row itself) — a dbo.BillCredit row
        only appears here because it already carries a QboId, so "is this
        mapped" is true by construction, with no separate
        qbo.VendorCreditBillCredit mapping-table read needed. Shares the
        missing-locally detector's cached read (_vendor_credit_identity_rows)
        rather than re-fetching the same realm-scoped set independently.
        """
        from integrations.intuit.qbo.base.delete_reconcile import (
            detect_void_absent_candidates,
        )
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient

        all_dbo_bill_credits = self._vendor_credit_identity_rows(realm_id)

        # Nothing mapped locally means there is no diff to compute, so skip the
        # id fetch entirely — saving API calls is the whole point of this detector.
        # Deliberate delta: on an empty local set the old path would still fetch and
        # surface an id-fetch failure as errors=1. There is nothing to flag either
        # way, so a no-op run reports clean rather than burning ~20 calls to fail.
        if not all_dbo_bill_credits:
            return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}

        flagged = 0
        flagged_deduped = 0
        errors = 0

        with QboVendorCreditClient(realm_id=realm_id) as client:
            diff = detect_void_absent_candidates(
                local_rows=all_dbo_bill_credits,
                realm_id=realm_id,
                reconcile_run_id=run_id,
                log_prefix="qbo.reconcile.vendor_credit_qbo_voided",
                fetch_live_ids=client.query_all_vendor_credit_ids,
                confirm_get=client.get_vendor_credit,
                extract_qbo_id=lambda row: normalize_qbo_id(row.qbo_id),
                lookup_mapping=lambda row: row,
            )

            if diff.aborted and diff.abort_reason == "ceiling_exceeded":
                max_candidates = diff.ceiling
                self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    severity_override="high",
                    entity_type="BillCredit",
                    qbo_id=None,
                    realm_id=realm_id,
                    details=(
                        f"Void detection aborted: {diff.candidate_count} locally-mapped BillCredit(s) were absent "
                        f"from the QBO id query, above the {max_candidates} candidate ceiling "
                        f"({diff.mapped_count} mapped, {diff.live_count} live). Nothing was flagged - "
                        f"this is far more likely an incomplete id fetch than a mass deletion. "
                        f"Raise QBO_RECONCILE_VOID_MAX_CANDIDATES only after confirming in QBO."
                    ),
                    reconcile_run_id=run_id,
                )
                return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 1}

            errors += diff.errors

            for candidate in diff.confirmed_voids:
                local = candidate.local_row
                qbo_id = candidate.qbo_id
                flagged += 1
                key = (realm_id, "BillCredit", qbo_id)
                # fetched lazily on the first confirmed 404 — a run with no voids pays no
                # query; the instance cache keeps it to one fetch per run.
                void_keys = self._unresolved_keys(DRIFT_QBO_VOIDED)
                if key in void_keys:
                    flagged_deduped += 1
                    logger.info(
                        "qbo.reconcile.vendor_credit_qbo_voided.void_issue_deduped",
                        extra={
                            "event_name": "qbo.reconcile.vendor_credit_qbo_voided.void_issue_deduped",
                            "qbo_id": qbo_id,
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )
                    continue
                # A failed write must NOT suppress a later retry — suppression is only
                # ever justified by a row that really exists.
                if self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    entity_type="BillCredit",
                    qbo_id=qbo_id,
                    realm_id=realm_id,
                    details=(
                        f"QBO VendorCredit {qbo_id} is mapped locally "
                        f"(BillCredit id={local.id}) but returns 404 from QBO. "
                        f"Likely voided or deleted on the QBO side. Review "
                        f"before taking action — downstream invoices may "
                        f"reference this bill credit."
                    ),
                    reconcile_run_id=run_id,
                ):
                    void_keys.add(key)

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": errors}

    # ------------------------------------------------------------------ #
    # Billable-status drift detection (U-335)
    # ------------------------------------------------------------------ #

    def _reconcile_bill_billable_status_drift(self, realm_id: str, run_id: str) -> dict:
        """
        Bill branch of the billable_status_drift detector (U-335). Scans
        dbo.BillLineItem rows with IsBilled=1 whose mapped qbo.BillLine still
        carries BillableStatus='Billable', aggregates to one issue per
        drifting Bill (never per line), and dedupes against still-open issues
        from a prior run via _unresolved_keys(DRIFT_BILLABLE_STATUS_DRIFT).
        """
        from shared.api.money import to_decimal_or_none
        from shared.database import get_connection
        from integrations.intuit.qbo.base.ids import normalize_qbo_id

        flagged = 0
        flagged_deduped = 0

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    b.Id AS BillId,
                    CAST(b.PublicId AS NVARCHAR(50)) AS BillPublicId,
                    b.QboId AS QboBillId,
                    b.BillNumber,
                    bli.Id AS BillLineItemId,
                    bli.Amount AS LineAmount,
                    inv.InvoiceNumber AS InvoiceNumber
                FROM dbo.BillLineItem bli
                JOIN dbo.Bill b ON b.Id = bli.BillId
                JOIN qbo.BillLineItemBillLine map ON map.BillLineItemId = bli.Id
                JOIN qbo.BillLine ql ON ql.Id = map.QboBillLineId
                -- Cross-check the QBO staging line's own parent realm against the
                -- dbo-native RealmId (U-335 review finding): a mapping row left
                -- behind by an identity-theft-clear event (see
                -- reconciliation_recorder.py's record_identity_mapping_conflict)
                -- could otherwise point at a DIFFERENT realm's qbo.Bill/BillLine.
                -- That divergence is its own identity-conflict drift, already
                -- covered elsewhere — this detector must not misattribute it.
                JOIN qbo.Bill qb ON qb.Id = ql.QboBillId AND qb.RealmId = b.RealmId
                LEFT JOIN dbo.InvoiceLineItem ili ON ili.BillLineItemId = bli.Id
                LEFT JOIN dbo.Invoice inv ON inv.Id = ili.InvoiceId
                WHERE bli.IsBilled = 1
                  AND ql.BillableStatus = 'Billable'
                  AND b.RealmId = ?
                  AND b.QboId IS NOT NULL
                """,
                realm_id,
            )

            groups: dict = {}
            for row in cursor.fetchall():
                g = groups.setdefault(row.BillId, {
                    "public_id": row.BillPublicId,
                    "qbo_id": row.QboBillId,
                    "bill_number": row.BillNumber,
                    # BillLineItemId -> Decimal amount (or None). dbo.InvoiceLineItem
                    # has no UNIQUE constraint on BillLineItemId, so the LEFT JOIN
                    # above can fan out one drifting line into multiple rows (U-335
                    # review finding); keying by the source line's own id makes a
                    # fanned-out repeat a no-op (first write wins) instead of
                    # double-counting — one source of truth for count + amount.
                    "lines": {},
                    "invoice_numbers": set(),
                })
                g["lines"].setdefault(row.BillLineItemId, to_decimal_or_none(row.LineAmount))
                if row.InvoiceNumber:
                    g["invoice_numbers"].add(row.InvoiceNumber)

            dedup_keys = self._unresolved_keys(DRIFT_BILLABLE_STATUS_DRIFT)
            for g in groups.values():
                qbo_id = normalize_qbo_id(g["qbo_id"])
                if not qbo_id:
                    continue
                # flagged_deduped is a SUBSET of flagged (matches RECONCILE_COUNT_KEYS'
                # documented invariant above) — a re-seen drifting parent still counts
                # as flagged (it was really detected); only its duplicate issue-write
                # is suppressed. Do not add flagged + flagged_deduped together.
                flagged += 1
                key = (realm_id, "Bill", qbo_id)
                if key in dedup_keys:
                    flagged_deduped += 1
                    continue
                line_count = len(g["lines"])
                amount = sum((a for a in g["lines"].values() if a is not None), Decimal("0"))
                invoice_list = ", ".join(sorted(g["invoice_numbers"])) if g["invoice_numbers"] else "unknown"
                details = (
                    f"{line_count} line(s) totaling ${amount:.2f} on Bill "
                    f"{g['bill_number'] or qbo_id} are locally billed (IsBilled=1) but "
                    f"still show BillableStatus='Billable' in QBO (never auto-pushed) "
                    f"— QBO's Suggested Transactions will keep re-suggesting them. "
                    f"Billed via Invoice(s): {invoice_list}."
                )
                # A failed write must NOT suppress a later retry — suppression is
                # only ever justified by a row that really exists.
                if self._record_issue(
                    drift_type=DRIFT_BILLABLE_STATUS_DRIFT,
                    action="flagged",
                    entity_type="Bill",
                    entity_public_id=g["public_id"],
                    qbo_id=qbo_id,
                    realm_id=realm_id,
                    details=details,
                    reconcile_run_id=run_id,
                ):
                    dedup_keys.add(key)

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": 0}

    def _reconcile_purchase_billable_status_drift(self, realm_id: str, run_id: str) -> dict:
        """
        Purchase (Expense) branch of the billable_status_drift detector
        (U-335). Mirrors _reconcile_bill_billable_status_drift exactly, one
        level down the QBO object model: dbo.ExpenseLineItem ->
        qbo.PurchaseLineExpenseLineItem -> qbo.PurchaseLine.
        """
        from shared.api.money import to_decimal_or_none
        from shared.database import get_connection
        from integrations.intuit.qbo.base.ids import normalize_qbo_id

        flagged = 0
        flagged_deduped = 0

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    e.Id AS ExpenseId,
                    CAST(e.PublicId AS NVARCHAR(50)) AS ExpensePublicId,
                    e.QboId AS QboPurchaseId,
                    e.ReferenceNumber,
                    eli.Id AS ExpenseLineItemId,
                    eli.Amount AS LineAmount,
                    inv.InvoiceNumber AS InvoiceNumber
                FROM dbo.ExpenseLineItem eli
                JOIN dbo.Expense e ON e.Id = eli.ExpenseId
                JOIN qbo.PurchaseLineExpenseLineItem map ON map.ExpenseLineItemId = eli.Id
                JOIN qbo.PurchaseLine ql ON ql.Id = map.QboPurchaseLineId
                -- Same realm cross-check as the Bill branch above — see its comment.
                JOIN qbo.Purchase qp ON qp.Id = ql.QboPurchaseId AND qp.RealmId = e.RealmId
                LEFT JOIN dbo.InvoiceLineItem ili ON ili.ExpenseLineItemId = eli.Id
                LEFT JOIN dbo.Invoice inv ON inv.Id = ili.InvoiceId
                WHERE eli.IsBilled = 1
                  AND ql.BillableStatus = 'Billable'
                  AND e.RealmId = ?
                  AND e.QboId IS NOT NULL
                """,
                realm_id,
            )

            groups: dict = {}
            for row in cursor.fetchall():
                g = groups.setdefault(row.ExpenseId, {
                    "public_id": row.ExpensePublicId,
                    "qbo_id": row.QboPurchaseId,
                    "reference_number": row.ReferenceNumber,
                    # Same fan-out dedupe shape as the Bill branch above — see its comment.
                    "lines": {},
                    "invoice_numbers": set(),
                })
                g["lines"].setdefault(row.ExpenseLineItemId, to_decimal_or_none(row.LineAmount))
                if row.InvoiceNumber:
                    g["invoice_numbers"].add(row.InvoiceNumber)

            dedup_keys = self._unresolved_keys(DRIFT_BILLABLE_STATUS_DRIFT)
            for g in groups.values():
                qbo_id = normalize_qbo_id(g["qbo_id"])
                if not qbo_id:
                    continue
                # flagged_deduped is a SUBSET of flagged — see the matching comment
                # in _reconcile_bill_billable_status_drift above.
                flagged += 1
                key = (realm_id, "Expense", qbo_id)
                if key in dedup_keys:
                    flagged_deduped += 1
                    continue
                line_count = len(g["lines"])
                amount = sum((a for a in g["lines"].values() if a is not None), Decimal("0"))
                invoice_list = ", ".join(sorted(g["invoice_numbers"])) if g["invoice_numbers"] else "unknown"
                details = (
                    f"{line_count} line(s) totaling ${amount:.2f} on Purchase "
                    f"{g['reference_number'] or qbo_id} are locally billed (IsBilled=1) "
                    f"but still show BillableStatus='Billable' in QBO (never "
                    f"auto-pushed) — QBO's Suggested Transactions will keep "
                    f"re-suggesting them. Billed via Invoice(s): {invoice_list}."
                )
                if self._record_issue(
                    drift_type=DRIFT_BILLABLE_STATUS_DRIFT,
                    action="flagged",
                    entity_type="Expense",
                    entity_public_id=g["public_id"],
                    qbo_id=qbo_id,
                    realm_id=realm_id,
                    details=details,
                    reconcile_run_id=run_id,
                ):
                    dedup_keys.add(key)

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": 0}

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
    ) -> bool:
        """Write a reconciliation issue row. Best-effort and non-raising.

        Returns True when a durable row was written (create + log succeeded),
        False on failure. Void detectors use the return to decide whether a
        dedupe key may be cached; other callers may ignore it.
        """
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
            return True
        except Exception:
            # Recording the issue is best-effort; failing to record should
            # not crash the entire reconciliation run.
            logger.exception(
                f"Failed to record reconciliation issue "
                f"(drift={drift_type}, entity={entity_type}, qbo_id={qbo_id})"
            )
            return False
