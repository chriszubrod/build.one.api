# Python Standard Library Imports
import logging
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


SEVERITY_BY_DRIFT = {
    DRIFT_QBO_MISSING_LOCALLY: "low",
    DRIFT_LOCAL_MISSING_QBO: "medium",
    DRIFT_STALE_SYNC_TOKEN: "low",
    DRIFT_MISSING_MAPPING: "low",
    DRIFT_FIELD_MISMATCH: "medium",
    DRIFT_DUPLICATE_MAPPING: "high",
    DRIFT_QBO_VOIDED: "low",
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
        from integrations.intuit.qbo.bill.connector.bill.business.service import (
            BillBillConnector,
        )
        from integrations.intuit.qbo.bill.connector.bill.persistence.repo import (
            BillBillRepository,
        )
        from integrations.intuit.qbo.bill.persistence.repo import (
            QboBillRepository,
            QboBillLineRepository,
        )

        mapping_repo = BillBillRepository()
        qbo_bill_repo = QboBillRepository()
        qbo_bill_line_repo = QboBillLineRepository()
        connector = BillBillConnector()

        auto_fixed = 0
        errors = 0

        with QboBillClient(realm_id=realm_id) as client:
            qbo_bills = client.query_all_bills()

        logger.info(
            f"Reconciliation fetched {len(qbo_bills)} bills from QBO for realm {realm_id}"
        )

        for qbo_bill in qbo_bills:
            try:
                # Is the QboBill already in our local cache?
                local_qbo_bill = qbo_bill_repo.read_by_qbo_id(qbo_bill.id)

                if local_qbo_bill:
                    # Check mapping to local Bill
                    mapping = mapping_repo.read_by_qbo_bill_id(local_qbo_bill.id)
                    if mapping:
                        # Fully synced — nothing to do.
                        continue

                # Either QboBill is missing locally, OR it exists but has no
                # Bill mapping. Either way, pull+sync restores the state.
                lines = self._fetch_bill_lines(client, qbo_bill)

                try:
                    connector.sync_from_qbo_bill(qbo_bill=qbo_bill, qbo_bill_lines=lines)
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

        return {"auto_fixed": auto_fixed, "flagged": errors, "errors": errors}

    def _fetch_bill_lines(self, client, qbo_bill):
        """Read QBO Bill lines from the nested `line` attribute of the Bill."""
        # The Bill client's query returns bills with inline line items via the
        # `line` attribute on the Pydantic model. Use whatever is present.
        return getattr(qbo_bill, "line", None) or []

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
