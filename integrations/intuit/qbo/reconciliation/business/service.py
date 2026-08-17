# Python Standard Library Imports
import logging
import os
import uuid
from typing import Optional

# Local Imports
from integrations.intuit.qbo.base.drift_types import (
    DRIFT_DUPLICATE_MAPPING,
    DRIFT_FIELD_MISMATCH,
    DRIFT_INVOICE_DRAW_MISMATCH,
    DRIFT_LOCAL_MISSING_QBO,
    DRIFT_MISSING_MAPPING,
    DRIFT_QBO_MISSING_LOCALLY,
    DRIFT_QBO_VOIDED,
    DRIFT_STALE_SYNC_TOKEN,
)
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
}

# Counter keys rolled up from each detector into a reconcile run summary.
# flagged_deduped is a SUBSET of flagged: a re-seen void still counts as
# flagged (it was really detected + 404-confirmed); only its duplicate
# issue-write is suppressed. Do not add them together.
RECONCILE_COUNT_KEYS = ("auto_fixed", "flagged", "flagged_deduped", "errors")


# Sanity ceiling for the void query-diff. A diff that nominates more than this
# many records is far more likely to be a bad id-fetch than a real mass deletion,
# so the detector aborts with one summary issue instead of flagging them.
DEFAULT_VOID_MAX_CANDIDATES = 200


def _void_max_candidates() -> int:
    raw = os.getenv("QBO_RECONCILE_VOID_MAX_CANDIDATES", "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_VOID_MAX_CANDIDATES
    return value if value > 0 else DEFAULT_VOID_MAX_CANDIDATES


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
        # Per-run dedupe cache for qbo_voided keys. Scoped to ONE reconcile run:
        # ReconciliationService is constructed per-invocation (admin reconcile router,
        # scheduler _sync_reconcile_bills), so all three void detectors share one
        # fetch and the cache dies with the run. If this service ever becomes
        # long-lived or a singleton, invalidate this cache per run — otherwise it
        # goes stale against issues resolved in SQL mid-life.
        self._void_key_cache = None

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

    def _unresolved_void_keys(self) -> set:
        """Open qbo_voided dedupe keys for this reconcile run (see _void_key_cache).

        Returns the live per-run cache object (not a copy); callers may .add() a
        key they have just durably written, and that key is then visible to the
        other detectors in the same run — safe because entity_type is part of the key.
        """
        if self._void_key_cache is not None:
            return self._void_key_cache
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        try:
            rows = self.repo.read_unresolved_issue_keys_by_drift_type(DRIFT_QBO_VOIDED)
            keys = set()
            for realm_id, entity_type, qbo_id in rows:
                normalized = normalize_qbo_id(qbo_id)
                if not normalized:
                    continue
                keys.add((realm_id, entity_type, normalized))
            self._void_key_cache = keys
        except Exception:
            # Fail open: empty key set means every 404 writes its issue — exactly
            # pre-U-160 behaviour. Suppression must NEVER be the failure mode:
            # a duplicate row is cheap, a lost flag is not.
            logger.exception(
                "qbo.reconcile.void_dedupe.key_fetch_failed",
                extra={"event_name": "qbo.reconcile.void_dedupe.key_fetch_failed"},
            )
            self._void_key_cache = set()
        return self._void_key_cache

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
        """
        from integrations.intuit.qbo.base.errors import (
            QboAuthError,
            QboBudgetExceededError,
            QboNotFoundError,
            QboRateLimitError,
        )
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        from integrations.intuit.qbo.bill.external.client import QboBillClient
        from integrations.intuit.qbo.bill.connector.bill.persistence.repo import (
            BillBillRepository,
        )
        from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository

        mapping_repo = BillBillRepository()
        qbo_bill_repo = QboBillRepository()

        all_qbo_bills = qbo_bill_repo.read_by_realm_id(realm_id)

        # Nothing mapped locally means there is no diff to compute, so skip the
        # id fetch entirely — saving API calls is the whole point of this detector.
        # Deliberate delta: on an empty local set the old path would still fetch and
        # surface an id-fetch failure as errors=1. There is nothing to flag either
        # way, so a no-op run reports clean rather than burning ~20 calls to fail.
        if not all_qbo_bills:
            return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}

        flagged = 0
        flagged_deduped = 0
        errors = 0

        with QboBillClient(realm_id=realm_id) as client:
            # Layer 1+2 - complete-or-abort. The id fetch is strict (see query_all_bill_ids);
            # on ANY failure we re-raise so the caller records one error and this detector
            # flags NOTHING. Never diff against a doubtful id set.
            try:
                live_ids = set(client.query_all_bill_ids())
            except Exception:
                logger.exception(
                    "qbo.reconcile.bill_qbo_voided.id_fetch_failed",
                    extra={
                        "event_name": "qbo.reconcile.bill_qbo_voided.id_fetch_failed",
                        "realm_id": realm_id,
                        "reconcile_run_id": run_id,
                    },
                )
                raise

            # mapped - live = candidates (NOT flags). The live-id test runs before the
            # mapping lookup so the per-record DB read also drops to the candidate count.
            candidates = []
            for local_qbo_bill in all_qbo_bills:
                qbo_id = normalize_qbo_id(local_qbo_bill.qbo_id)
                if not qbo_id:
                    continue
                if qbo_id in live_ids:
                    continue
                mapping = mapping_repo.read_by_qbo_bill_id(local_qbo_bill.id)
                if not mapping:
                    continue
                candidates.append((local_qbo_bill, qbo_id, mapping))

            # Layer 2b - sanity gate. Above the ceiling, confirm nothing and flag nothing:
            # write ONE high-severity summary so the anomaly is loud but the issue table
            # is not flooded and no autofix can ever act on a diff artifact.
            max_candidates = _void_max_candidates()
            if len(candidates) > max_candidates:
                logger.error(
                    "qbo.reconcile.bill_qbo_voided.candidate_ceiling_exceeded",
                    extra={
                        "event_name": "qbo.reconcile.bill_qbo_voided.candidate_ceiling_exceeded",
                        "realm_id": realm_id,
                        "reconcile_run_id": run_id,
                        "candidate_count": len(candidates),
                        "max_candidates": max_candidates,
                    },
                )
                self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    severity_override="high",
                    entity_type="Bill",
                    qbo_id=None,
                    realm_id=realm_id,
                    details=(
                        f"Void detection aborted: {len(candidates)} locally-mapped Bill(s) were absent "
                        f"from the QBO id query, above the {max_candidates} candidate ceiling "
                        f"({len(all_qbo_bills)} mapped, {len(live_ids)} live). Nothing was flagged - "
                        f"this is far more likely an incomplete id fetch than a mass deletion. "
                        f"Raise QBO_RECONCILE_VOID_MAX_CANDIDATES only after confirming in QBO."
                    ),
                    reconcile_run_id=run_id,
                )
                return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 1}

            # Layer 3 - confirm each candidate with the same GET the old scan used, so every
            # flag is still backed by a real 404 and an id set that was complete-looking but
            # wrong degrades to a wasted GET rather than a false issue.
            for local_qbo_bill, qbo_id, mapping in candidates:
                try:
                    client.get_bill(qbo_id)
                except QboNotFoundError:
                    flagged += 1
                    key = (realm_id, "Bill", qbo_id)
                    # fetched lazily on the first confirmed 404 — a run with no voids pays no
                    # query; the instance cache keeps it to one fetch per run.
                    void_keys = self._unresolved_void_keys()
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
                            f"(local QboBill id={local_qbo_bill.id}, mapped to "
                            f"Bill id={mapping.bill_id}) but returns 404 from QBO. "
                            f"Likely voided or deleted on the QBO side. Review "
                            f"before taking action — downstream invoices may "
                            f"reference this bill."
                        ),
                        reconcile_run_id=run_id,
                    ):
                        void_keys.add(key)
                except (QboAuthError, QboBudgetExceededError, QboRateLimitError):
                    # U-212 backport from base/delete_reconcile.py: systemic —
                    # no later candidate's confirm can succeed; stop burning
                    # one metered call per remaining candidate.
                    errors += 1
                    logger.warning(
                        "qbo.reconcile.bill_qbo_voided.confirm_aborted_systemic",
                        extra={
                            "event_name": "qbo.reconcile.bill_qbo_voided.confirm_aborted_systemic",
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )
                    break
                except Exception:
                    errors += 1
                    logger.exception(
                        f"qbo.reconcile.bill_qbo_voided.detector_error for "
                        f"qbo_id={qbo_id}"
                    )
                else:
                    logger.warning(
                        "qbo.reconcile.bill_qbo_voided.diff_false_positive",
                        extra={
                            "event_name": "qbo.reconcile.bill_qbo_voided.diff_false_positive",
                            "qbo_id": qbo_id,
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": errors}

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

        Strategy: page the live id list once, diff against local mappings,
        confirm each candidate with a single GET. Coverage equals the prior
        per-record scan for any run at or below the candidate ceiling: only
        hard-deleted records 404; QBO-voided-but-present records return 200 and
        appear in the query, so neither the old scan nor this one flags them.
        ABOVE the ceiling the detector deliberately trades coverage for safety
        — it flags nothing and records one summary issue, because a candidate
        set that large is far more likely to be a bad id fetch than a real mass
        deletion.
        """
        from integrations.intuit.qbo.base.errors import (
            QboAuthError,
            QboBudgetExceededError,
            QboNotFoundError,
            QboRateLimitError,
        )
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
        from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import (
            PurchaseExpenseRepository,
        )
        from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

        mapping_repo = PurchaseExpenseRepository()
        qbo_purchase_repo = QboPurchaseRepository()

        all_qbo_purchases = qbo_purchase_repo.read_by_realm_id(realm_id)

        # Nothing mapped locally means there is no diff to compute, so skip the
        # id fetch entirely — saving API calls is the whole point of this detector.
        # Deliberate delta: on an empty local set the old path would still fetch and
        # surface an id-fetch failure as errors=1. There is nothing to flag either
        # way, so a no-op run reports clean rather than burning ~20 calls to fail.
        if not all_qbo_purchases:
            return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}

        flagged = 0
        flagged_deduped = 0
        errors = 0

        with QboPurchaseClient(realm_id=realm_id) as client:
            try:
                live_ids = set(client.query_all_purchase_ids())
            except Exception:
                logger.exception(
                    "qbo.reconcile.purchase_qbo_voided.id_fetch_failed",
                    extra={
                        "event_name": "qbo.reconcile.purchase_qbo_voided.id_fetch_failed",
                        "realm_id": realm_id,
                        "reconcile_run_id": run_id,
                    },
                )
                raise

            candidates = []
            for local in all_qbo_purchases:
                qbo_id = normalize_qbo_id(local.qbo_id)
                if not qbo_id:
                    continue
                if qbo_id in live_ids:
                    continue
                mapping = mapping_repo.read_by_qbo_purchase_id(local.id)
                if not mapping:
                    continue
                candidates.append((local, qbo_id, mapping))

            max_candidates = _void_max_candidates()
            if len(candidates) > max_candidates:
                logger.error(
                    "qbo.reconcile.purchase_qbo_voided.candidate_ceiling_exceeded",
                    extra={
                        "event_name": "qbo.reconcile.purchase_qbo_voided.candidate_ceiling_exceeded",
                        "realm_id": realm_id,
                        "reconcile_run_id": run_id,
                        "candidate_count": len(candidates),
                        "max_candidates": max_candidates,
                    },
                )
                self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    severity_override="high",
                    entity_type="Expense",
                    qbo_id=None,
                    realm_id=realm_id,
                    details=(
                        f"Void detection aborted: {len(candidates)} locally-mapped Expense(s) were absent "
                        f"from the QBO id query, above the {max_candidates} candidate ceiling "
                        f"({len(all_qbo_purchases)} mapped, {len(live_ids)} live). Nothing was flagged - "
                        f"this is far more likely an incomplete id fetch than a mass deletion. "
                        f"Raise QBO_RECONCILE_VOID_MAX_CANDIDATES only after confirming in QBO."
                    ),
                    reconcile_run_id=run_id,
                )
                return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 1}

            for local, qbo_id, mapping in candidates:
                try:
                    client.get_purchase(qbo_id)
                except QboNotFoundError:
                    flagged += 1
                    key = (realm_id, "Expense", qbo_id)
                    # fetched lazily on the first confirmed 404 — a run with no voids pays no
                    # query; the instance cache keeps it to one fetch per run.
                    void_keys = self._unresolved_void_keys()
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
                            f"(local QboPurchase id={local.id}, mapped to "
                            f"Expense id={mapping.expense_id}) but returns 404 from QBO. "
                            f"Likely voided or deleted on the QBO side. Review "
                            f"before taking action — downstream invoices may "
                            f"reference this expense."
                        ),
                        reconcile_run_id=run_id,
                    ):
                        void_keys.add(key)
                except (QboAuthError, QboBudgetExceededError, QboRateLimitError):
                    # U-212 backport from base/delete_reconcile.py: systemic —
                    # no later candidate's confirm can succeed; stop burning
                    # one metered call per remaining candidate.
                    errors += 1
                    logger.warning(
                        "qbo.reconcile.purchase_qbo_voided.confirm_aborted_systemic",
                        extra={
                            "event_name": "qbo.reconcile.purchase_qbo_voided.confirm_aborted_systemic",
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )
                    break
                except Exception:
                    errors += 1
                    logger.exception(
                        f"qbo.reconcile.purchase_qbo_voided.detector_error for "
                        f"qbo_id={qbo_id}"
                    )
                else:
                    logger.warning(
                        "qbo.reconcile.purchase_qbo_voided.diff_false_positive",
                        extra={
                            "event_name": "qbo.reconcile.purchase_qbo_voided.diff_false_positive",
                            "qbo_id": qbo_id,
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": errors}

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

        Strategy: page the live id list once, diff against local mappings,
        confirm each candidate with a single GET. Coverage equals the prior
        per-record scan for any run at or below the candidate ceiling: only
        hard-deleted records 404; QBO-voided-but-present records return 200 and
        appear in the query, so neither the old scan nor this one flags them.
        ABOVE the ceiling the detector deliberately trades coverage for safety
        — it flags nothing and records one summary issue, because a candidate
        set that large is far more likely to be a bad id fetch than a real mass
        deletion.
        """
        from integrations.intuit.qbo.base.errors import (
            QboAuthError,
            QboBudgetExceededError,
            QboNotFoundError,
            QboRateLimitError,
        )
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import (
            VendorCreditBillCreditMappingRepository,
        )
        from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository

        mapping_repo = VendorCreditBillCreditMappingRepository()
        qbo_vc_repo = QboVendorCreditRepository()

        all_qbo_vcs = qbo_vc_repo.read_by_realm_id(realm_id)

        # Nothing mapped locally means there is no diff to compute, so skip the
        # id fetch entirely — saving API calls is the whole point of this detector.
        # Deliberate delta: on an empty local set the old path would still fetch and
        # surface an id-fetch failure as errors=1. There is nothing to flag either
        # way, so a no-op run reports clean rather than burning ~20 calls to fail.
        if not all_qbo_vcs:
            return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 0}

        flagged = 0
        flagged_deduped = 0
        errors = 0

        with QboVendorCreditClient(realm_id=realm_id) as client:
            try:
                live_ids = set(client.query_all_vendor_credit_ids())
            except Exception:
                logger.exception(
                    "qbo.reconcile.vendor_credit_qbo_voided.id_fetch_failed",
                    extra={
                        "event_name": "qbo.reconcile.vendor_credit_qbo_voided.id_fetch_failed",
                        "realm_id": realm_id,
                        "reconcile_run_id": run_id,
                    },
                )
                raise

            candidates = []
            for local in all_qbo_vcs:
                qbo_id = normalize_qbo_id(local.qbo_id)
                if not qbo_id:
                    continue
                if qbo_id in live_ids:
                    continue
                mapping = mapping_repo.read_by_qbo_vendor_credit_id(local.id)
                if not mapping:
                    continue
                candidates.append((local, qbo_id, mapping))

            max_candidates = _void_max_candidates()
            if len(candidates) > max_candidates:
                logger.error(
                    "qbo.reconcile.vendor_credit_qbo_voided.candidate_ceiling_exceeded",
                    extra={
                        "event_name": "qbo.reconcile.vendor_credit_qbo_voided.candidate_ceiling_exceeded",
                        "realm_id": realm_id,
                        "reconcile_run_id": run_id,
                        "candidate_count": len(candidates),
                        "max_candidates": max_candidates,
                    },
                )
                self._record_issue(
                    drift_type=DRIFT_QBO_VOIDED,
                    action="flagged",
                    severity_override="high",
                    entity_type="BillCredit",
                    qbo_id=None,
                    realm_id=realm_id,
                    details=(
                        f"Void detection aborted: {len(candidates)} locally-mapped BillCredit(s) were absent "
                        f"from the QBO id query, above the {max_candidates} candidate ceiling "
                        f"({len(all_qbo_vcs)} mapped, {len(live_ids)} live). Nothing was flagged - "
                        f"this is far more likely an incomplete id fetch than a mass deletion. "
                        f"Raise QBO_RECONCILE_VOID_MAX_CANDIDATES only after confirming in QBO."
                    ),
                    reconcile_run_id=run_id,
                )
                return {"auto_fixed": 0, "flagged": 0, "flagged_deduped": 0, "errors": 1}

            for local, qbo_id, mapping in candidates:
                try:
                    client.get_vendor_credit(qbo_id)
                except QboNotFoundError:
                    flagged += 1
                    key = (realm_id, "BillCredit", qbo_id)
                    # fetched lazily on the first confirmed 404 — a run with no voids pays no
                    # query; the instance cache keeps it to one fetch per run.
                    void_keys = self._unresolved_void_keys()
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
                            f"(local QboVendorCredit id={local.id}, mapped to "
                            f"BillCredit id={mapping.bill_credit_id}) but returns 404 from QBO. "
                            f"Likely voided or deleted on the QBO side. Review "
                            f"before taking action — downstream invoices may "
                            f"reference this bill credit."
                        ),
                        reconcile_run_id=run_id,
                    ):
                        void_keys.add(key)
                except (QboAuthError, QboBudgetExceededError, QboRateLimitError):
                    # U-212 backport from base/delete_reconcile.py: systemic —
                    # no later candidate's confirm can succeed; stop burning
                    # one metered call per remaining candidate.
                    errors += 1
                    logger.warning(
                        "qbo.reconcile.vendor_credit_qbo_voided.confirm_aborted_systemic",
                        extra={
                            "event_name": "qbo.reconcile.vendor_credit_qbo_voided.confirm_aborted_systemic",
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )
                    break
                except Exception:
                    errors += 1
                    logger.exception(
                        f"qbo.reconcile.vendor_credit_qbo_voided.detector_error for "
                        f"qbo_id={qbo_id}"
                    )
                else:
                    logger.warning(
                        "qbo.reconcile.vendor_credit_qbo_voided.diff_false_positive",
                        extra={
                            "event_name": "qbo.reconcile.vendor_credit_qbo_voided.diff_false_positive",
                            "qbo_id": qbo_id,
                            "realm_id": realm_id,
                            "reconcile_run_id": run_id,
                        },
                    )

        return {"auto_fixed": 0, "flagged": flagged, "flagged_deduped": flagged_deduped, "errors": errors}

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
