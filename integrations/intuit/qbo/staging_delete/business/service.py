"""
Remove confirmed-deleted QBO records from qbo.* staging (CDC + full id-diff).

Owns:
  - CDC and full-mode detection of QBO deletions against local qbo.* staging.
  - GET-confirm before any staging header delete (lines cascade via FK).
  - Invoice dbo binding flagged via [qbo].[ReconciliationIssue] (qbo_deleted).

Invariants:
  - dbo rows are never deleted or mutated by this service.
  - Staging deletes only after individual GET raises QboNotFoundError.
  - Candidate ceiling matches pull delete-reconcile (QBO_PULL_DELETE_MAX_CANDIDATES).
  - Invoice staging delete is skipped when a new qbo_deleted issue write fails.

External writes:
  - None (QBO read-only; issue rows are reconciliation metadata only).

Sprocs used:
  - DeleteQbo{Bill,Purchase,VendorCredit,Invoice}ByQboIdAndRealmId (U-718 staging delete)
  - DeleteQboReimburseChargeByQboIdAndRealmId (U-718)
  - CreateQboReconciliationIssue / ReadQboUnresolvedIssueKeysByDriftType
"""
# Python Standard Library Imports
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set

# Local Imports
from integrations.intuit.qbo.base.cdc import (
    STAGING_DELETE_CDC_MAX_LOOKBACK_HOURS,
    fetch_cdc_deleted_ids,
)
from integrations.intuit.qbo.base.delete_reconcile import (
    _delete_max_candidates,
    confirm_deleted_ids,
    strict_confirmed_deleted_ids,
)
from integrations.intuit.qbo.base.ids import normalize_qbo_id
from shared.env_flags import _env_positive_int

logger = logging.getLogger(__name__)

_ALL_ENTITIES = ("Bill", "Purchase", "VendorCredit", "Invoice", "ReimburseCharge")
_DEFAULT_CDC_LOOKBACK_HOURS = 48


def _empty_entity_counts() -> Dict[str, Any]:
    return {
        "candidates": 0,
        "confirmed_deleted": 0,
        "alive_or_unconfirmed": 0,
        "staging_deleted": 0,
        "staging_delete_errors": 0,
        "dbo_still_bound": 0,
        "invoices_flagged": 0,
        "flag_errors": 0,
        "binding_read_errors": 0,
        "aborted": None,
    }


def _clamp_lookback_hours(hours: Optional[int]) -> int:
    if hours is None:
        raw = _env_positive_int(
            "QBO_STAGING_DELETE_CDC_LOOKBACK_HOURS",
            _DEFAULT_CDC_LOOKBACK_HOURS,
            minimum=1,
            warn=False,
        )
    else:
        raw = int(hours)
    return max(1, min(STAGING_DELETE_CDC_MAX_LOOKBACK_HOURS, raw))


@dataclass
class _EntityRuntime:
    name: str
    staging_repo: Any
    confirm_get: Callable[[str], object]
    fetch_live_ids: Callable[[], List[str]]
    dbo_still_bound: Optional[Callable[[str, str], bool]] = None


class QboStagingDeleteReconcileService:
    """CDC + full reconcile to drop ghost rows from qbo.* staging."""

    def __init__(
        self,
        *,
        bill_repo: Optional[Any] = None,
        purchase_repo: Optional[Any] = None,
        vendor_credit_repo: Optional[Any] = None,
        invoice_repo: Optional[Any] = None,
        reimburse_charge_repo: Optional[Any] = None,
        bill_client: Optional[Any] = None,
        purchase_client: Optional[Any] = None,
        vendor_credit_client: Optional[Any] = None,
        invoice_client: Optional[Any] = None,
        bill_service_factory: Optional[Callable[[], Any]] = None,
        expense_service_factory: Optional[Callable[[], Any]] = None,
        bill_credit_service_factory: Optional[Callable[[], Any]] = None,
        invoice_service_factory: Optional[Callable[[], Any]] = None,
        reconciliation_repo_factory: Optional[Callable[[], Any]] = None,
    ):
        if bill_repo is None:
            from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository

            bill_repo = QboBillRepository()
        if purchase_repo is None:
            from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

            purchase_repo = QboPurchaseRepository()
        if vendor_credit_repo is None:
            from integrations.intuit.qbo.vendorcredit.persistence.repo import (
                QboVendorCreditRepository,
            )

            vendor_credit_repo = QboVendorCreditRepository()
        if invoice_repo is None:
            from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository

            invoice_repo = QboInvoiceRepository()
        if reimburse_charge_repo is None:
            from integrations.intuit.qbo.reimburse_charge.persistence.repo import (
                QboReimburseChargeRepository,
            )

            reimburse_charge_repo = QboReimburseChargeRepository()

        self._bill_repo = bill_repo
        self._purchase_repo = purchase_repo
        self._vendor_credit_repo = vendor_credit_repo
        self._invoice_repo = invoice_repo
        self._reimburse_charge_repo = reimburse_charge_repo
        self._bill_client = bill_client
        self._purchase_client = purchase_client
        self._vendor_credit_client = vendor_credit_client
        self._invoice_client = invoice_client
        self._bill_service_factory = bill_service_factory or self._bill_service
        self._expense_service_factory = expense_service_factory or self._expense_service
        self._bill_credit_service_factory = bill_credit_service_factory or (
            self._bill_credit_service
        )
        self._invoice_service_factory = invoice_service_factory or self._invoice_service
        self._reconciliation_repo_factory = reconciliation_repo_factory or (
            self._reconciliation_repo
        )

    @staticmethod
    def _bill_service():
        from entities.bill.business.service import BillService

        return BillService()

    @staticmethod
    def _expense_service():
        from entities.expense.business.service import ExpenseService

        return ExpenseService()

    @staticmethod
    def _bill_credit_service():
        from entities.bill_credit.business.service import BillCreditService

        return BillCreditService()

    @staticmethod
    def _invoice_service():
        from entities.invoice.business.service import InvoiceService

        return InvoiceService()

    @staticmethod
    def _reconciliation_repo():
        from integrations.intuit.qbo.reconciliation.persistence.repo import (
            ReconciliationIssueRepository,
        )

        return ReconciliationIssueRepository()

    def _ensure_clients(self, realm_id: str) -> None:
        if self._bill_client is None:
            from integrations.intuit.qbo.bill.external.client import QboBillClient

            self._bill_client = QboBillClient(realm_id=realm_id)
        if self._purchase_client is None:
            from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient

            self._purchase_client = QboPurchaseClient(realm_id=realm_id)
        if self._vendor_credit_client is None:
            from integrations.intuit.qbo.vendorcredit.external.client import (
                QboVendorCreditClient,
            )

            self._vendor_credit_client = QboVendorCreditClient(realm_id=realm_id)
        if self._invoice_client is None:
            from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient

            self._invoice_client = QboInvoiceClient(realm_id=realm_id)

    def _entity_runtimes(self) -> Dict[str, _EntityRuntime]:
        return {
            "Bill": _EntityRuntime(
                name="Bill",
                staging_repo=self._bill_repo,
                confirm_get=self._bill_client.get_bill,
                fetch_live_ids=self._bill_client.query_all_bill_ids,
                dbo_still_bound=lambda qid, rid: self._bill_service_factory().read_by_qbo_identity(
                    qid, rid
                )
                is not None,
            ),
            "Purchase": _EntityRuntime(
                name="Purchase",
                staging_repo=self._purchase_repo,
                confirm_get=self._purchase_client.get_purchase,
                fetch_live_ids=self._purchase_client.query_all_purchase_ids,
                dbo_still_bound=lambda qid, rid: self._expense_service_factory().read_by_qbo_identity(
                    qid, rid
                )
                is not None,
            ),
            "VendorCredit": _EntityRuntime(
                name="VendorCredit",
                staging_repo=self._vendor_credit_repo,
                confirm_get=self._vendor_credit_client.get_vendor_credit,
                fetch_live_ids=self._vendor_credit_client.query_all_vendor_credit_ids,
                dbo_still_bound=lambda qid, rid: self._bill_credit_service_factory().read_by_qbo_identity(
                    qid, rid
                )
                is not None,
            ),
            "Invoice": _EntityRuntime(
                name="Invoice",
                staging_repo=self._invoice_repo,
                confirm_get=self._invoice_client.get_invoice,
                fetch_live_ids=self._invoice_client.query_all_invoice_ids,
            ),
            "ReimburseCharge": _EntityRuntime(
                name="ReimburseCharge",
                staging_repo=self._reimburse_charge_repo,
                confirm_get=self._invoice_client.get_reimburse_charge,
                fetch_live_ids=self._invoice_client.query_all_reimburse_charge_ids,
            ),
        }

    def _act_on_confirmed(
        self,
        entity_name: str,
        realm_id: str,
        qbo_id: str,
        *,
        dry_run: bool,
        counts: Optional[Dict[str, Any]] = None,
        runtime: Optional[_EntityRuntime] = None,
    ) -> None:
        if counts is None:
            counts = _empty_entity_counts()
        if runtime is None:
            runtime = self._entity_runtimes()[entity_name]

        if entity_name == "Invoice":
            try:
                dbo = self._invoice_service_factory().read_by_qbo_identity(qbo_id, realm_id)
            except Exception:
                counts["binding_read_errors"] += 1
                logger.exception(
                    "qbo.staging_delete.invoice_binding_read_failed",
                    extra={
                        "event_name": "qbo.staging_delete.invoice_binding_read_failed",
                        "realm_id": realm_id,
                        "qbo_id": qbo_id,
                    },
                )
                return
            if dbo is not None:
                if not self._ensure_invoice_issue(dbo, realm_id, qbo_id, counts, dry_run):
                    return

        elif runtime.dbo_still_bound:
            try:
                still_bound = runtime.dbo_still_bound(qbo_id, realm_id)
            except Exception:
                counts["binding_read_errors"] += 1
                logger.exception(
                    "qbo.staging_delete.binding_read_failed",
                    extra={
                        "event_name": "qbo.staging_delete.binding_read_failed",
                        "entity_type": entity_name,
                        "realm_id": realm_id,
                        "qbo_id": qbo_id,
                    },
                )
                return
            if still_bound:
                counts["dbo_still_bound"] += 1

        if dry_run:
            counts["staging_deleted"] += 1
            return
        try:
            runtime.staging_repo.delete_by_qbo_id_and_realm_id(qbo_id, realm_id)
            counts["staging_deleted"] += 1
        except Exception:
            counts["staging_delete_errors"] += 1
            logger.exception(
                "qbo.staging_delete.staging_delete_failed",
                extra={
                    "event_name": "qbo.staging_delete.staging_delete_failed",
                    "entity_type": entity_name,
                    "realm_id": realm_id,
                    "qbo_id": qbo_id,
                },
            )

    def _ensure_invoice_issue(
        self,
        dbo: Any,
        realm_id: str,
        qbo_id: str,
        counts: Dict[str, Any],
        dry_run: bool,
    ) -> bool:
        """Return False when staging delete must be skipped (issue write failed)."""
        reconciliation_repo = self._reconciliation_repo_factory()
        try:
            existing = {
                (r, e, normalize_qbo_id(q))
                for r, e, q in reconciliation_repo.read_unresolved_issue_keys_by_drift_type(
                    "qbo_deleted"
                )
                if normalize_qbo_id(q)
            }
        except Exception:
            logger.exception("qbo.staging_delete.issue_keys_read_failed")
            counts["flag_errors"] += 1
            return False

        norm_id = normalize_qbo_id(qbo_id)
        key = (realm_id, "Invoice", norm_id)
        if key not in existing:
            if dry_run:
                counts["invoices_flagged"] += 1
                return True
            details = (
                f"invoice_number={getattr(dbo, 'invoice_number', None)!r}, "
                f"total={getattr(dbo, 'total_amount', None)!r}, "
                f"is_draft={getattr(dbo, 'is_draft', None)!r}; "
                "deleted in QBO; dbo invoice untouched — decide whether to delete it"
            )
            try:
                reconciliation_repo.create(
                    drift_type="qbo_deleted",
                    severity="high",
                    action="flagged",
                    entity_type="Invoice",
                    realm_id=realm_id,
                    qbo_id=qbo_id,
                    entity_public_id=getattr(dbo, "public_id", None),
                    details=details,
                )
                counts["invoices_flagged"] += 1
            except Exception:
                logger.exception("qbo.staging_delete.invoice_issue_write_failed")
                counts["flag_errors"] += 1
                return False
        return True

    def _process_confirmed_set(
        self,
        entity_name: str,
        realm_id: str,
        confirmed: Set[str],
        *,
        dry_run: bool,
        counts: Dict[str, Any],
        runtime: _EntityRuntime,
    ) -> None:
        counts["confirmed_deleted"] = len(confirmed)
        for qbo_id in sorted(confirmed):
            self._act_on_confirmed(
                entity_name,
                realm_id,
                qbo_id,
                dry_run=dry_run,
                counts=counts,
                runtime=runtime,
            )

    def run_cdc(
        self,
        realm_id: str,
        *,
        lookback_hours: Optional[int] = None,
        dry_run: bool = False,
    ) -> dict:
        self._ensure_clients(realm_id)
        runtimes = self._entity_runtimes()
        entities_out: Dict[str, Dict[str, Any]] = {
            name: _empty_entity_counts() for name in _ALL_ENTITIES
        }

        hours = _clamp_lookback_hours(lookback_hours)
        changed_since = datetime.now(timezone.utc) - timedelta(hours=hours)
        try:
            deleted_by_entity = fetch_cdc_deleted_ids(
                self._bill_client._http_client,
                entities=list(_ALL_ENTITIES),
                changed_since=changed_since,
            )
        except Exception:
            logger.exception("qbo.staging_delete.cdc_fetch_failed")
            for name in _ALL_ENTITIES:
                entities_out[name]["aborted"] = "cdc_fetch_failed"
            return {
                "mode": "cdc",
                "dry_run": dry_run,
                "lookback_hours": hours,
                "entities": entities_out,
            }

        ceiling = _delete_max_candidates()

        for name in _ALL_ENTITIES:
            counts = entities_out[name]
            runtime = runtimes[name]
            try:
                deleted_ids = deleted_by_entity.get(name) or set()
                candidates: List[str] = []
                for qbo_id in deleted_ids:
                    if runtime.staging_repo.read_by_qbo_id_and_realm_id(qbo_id, realm_id):
                        candidates.append(qbo_id)

                counts["candidates"] = len(candidates)
                if not candidates:
                    continue

                if len(candidates) > ceiling:
                    counts["aborted"] = "candidate_ceiling_exceeded"
                    logger.error(
                        "qbo.staging_delete.candidate_ceiling_exceeded",
                        extra={
                            "event_name": "qbo.staging_delete.candidate_ceiling_exceeded",
                            "entity_type": name,
                            "realm_id": realm_id,
                            "candidate_count": len(candidates),
                            "max_candidates": ceiling,
                        },
                    )
                    continue

                confirmed = confirm_deleted_ids(
                    entity_type=name,
                    realm_id=realm_id,
                    candidates=candidates,
                    confirm_get=runtime.confirm_get,
                )
                if confirmed is None:
                    counts["aborted"] = "systemic_confirm_abort"
                    continue

                counts["alive_or_unconfirmed"] = len(candidates) - len(confirmed)
                self._process_confirmed_set(
                    name,
                    realm_id,
                    confirmed,
                    dry_run=dry_run,
                    counts=counts,
                    runtime=runtime,
                )
            except Exception:
                logger.exception(
                    "qbo.staging_delete.entity_processing_failed",
                    extra={"entity_type": name, "realm_id": realm_id},
                )
                counts["aborted"] = "entity_processing_failed"

        return {
            "mode": "cdc",
            "dry_run": dry_run,
            "lookback_hours": hours,
            "entities": entities_out,
        }

    def run_full(
        self,
        realm_id: str,
        *,
        entities: Optional[List[str]] = None,
        max_candidates: Optional[int] = None,
        dry_run: bool = False,
    ) -> dict:
        self._ensure_clients(realm_id)
        runtimes = self._entity_runtimes()
        target = tuple(entities) if entities else _ALL_ENTITIES
        entities_out: Dict[str, Dict[str, Any]] = {
            name: _empty_entity_counts() for name in _ALL_ENTITIES
        }

        for name in target:
            if name not in runtimes:
                continue
            counts = entities_out[name]
            runtime = runtimes[name]
            try:
                try:
                    rows = runtime.staging_repo.read_by_realm_id(realm_id)
                except Exception:
                    logger.exception(
                        "qbo.staging_delete.read_staging_failed",
                        extra={"entity_type": name, "realm_id": realm_id},
                    )
                    counts["aborted"] = "staging_read_failed"
                    continue

                local_ids = [getattr(r, "qbo_id", None) for r in rows]
                live_cache: Dict[str, Any] = {"ids": None, "error": None}

                def fetch_live_ids_cached() -> List[str]:
                    if live_cache["error"] is not None:
                        raise live_cache["error"]
                    if live_cache["ids"] is None:
                        try:
                            live_cache["ids"] = runtime.fetch_live_ids()
                        except Exception as exc:
                            live_cache["error"] = exc
                            raise
                    return live_cache["ids"]

                try:
                    confirmed = strict_confirmed_deleted_ids(
                        entity_type=name,
                        realm_id=realm_id,
                        fetch_live_ids=fetch_live_ids_cached,
                        confirm_get=runtime.confirm_get,
                        local_qbo_ids=local_ids,
                        max_candidates=max_candidates,
                        record_issue=not dry_run,
                    )
                except Exception:
                    logger.exception(
                        "qbo.staging_delete.full_reconcile_failed",
                        extra={"entity_type": name, "realm_id": realm_id},
                    )
                    counts["aborted"] = "reconcile_failed"
                    continue

                if confirmed is None:
                    counts["aborted"] = "reconcile_aborted"
                    continue

                try:
                    live_raw = fetch_live_ids_cached()
                except Exception:
                    logger.exception(
                        "qbo.staging_delete.full_reconcile_failed",
                        extra={"entity_type": name, "realm_id": realm_id},
                    )
                    counts["aborted"] = "reconcile_failed"
                    continue

                live_norm = {normalize_qbo_id(i) for i in live_raw}
                live_norm.discard(None)
                norm_local = [n for n in (normalize_qbo_id(i) for i in local_ids) if n]
                absent = [i for i in norm_local if i not in live_norm]
                counts["candidates"] = len(absent)
                counts["alive_or_unconfirmed"] = max(0, len(absent) - len(confirmed))

                self._process_confirmed_set(
                    name,
                    realm_id,
                    confirmed,
                    dry_run=dry_run,
                    counts=counts,
                    runtime=runtime,
                )
            except Exception:
                logger.exception(
                    "qbo.staging_delete.entity_processing_failed",
                    extra={"entity_type": name, "realm_id": realm_id},
                )
                counts["aborted"] = "entity_processing_failed"

        return {"mode": "full", "dry_run": dry_run, "entities": entities_out}
