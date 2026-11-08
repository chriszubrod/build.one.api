"""
Billable-status refresh sweep for QBO Bill / Purchase / VendorCredit staging lines.

Owns:
  - Forward pass: detecting staged qbo.*Line rows still marked Billable whose live QBO line differs.
  - Forward pass: compare-and-set updates to qbo.*Line.BillableStatus (Billable -> HasBeenBilled/NotBillable).
  - Forward pass: dbo BillLineItem / ExpenseLineItem / BillCreditLineItem IsBilled 0->1 heal when live is
    HasBeenBilled.
  - Reverse pass (U-778b): a staged HasBeenBilled line whose staged ReimburseCharge says
    HasBeenInvoiced=0 (QBO un-invoiced it) is re-fetched live; only when the live line is Billable AND
    carries the same ReimburseCharge id does the dbo source line go IsBilled 1->0 (guarded) and staging
    CAS HasBeenBilled -> Billable.

Invariants:
  - Forward staging writes touch BillableStatus (+ ModifiedDatetime) only via CAS from Billable.
  - Only HasBeenBilled and NotBillable may be written to staging by the forward pass; other live values are
    skipped.
  - dbo heal goes through update_by_public_id (round-trips the stored row). ExpenseLineItem's
    read sproc truncates BillableDecidedAt to whole seconds on that round trip (pre-existing,
    shared by every caller incl. invoice completion). Forward heal sets is_billed True only and passes no
    other field.
  - HasBeenBilled dbo heal runs before staging CAS; CAS runs only after heal completes without error.
  - The reverse flip is this service's only 1->0 path, and it is guarded (the Bill pull connector also writes
    IsBilled from the live status on a re-pull, unguarded — pre-existing, booked in TODO.md under U-778): staging
    may be CAS'd HasBeenBilled -> Billable only
    on a live Billable read whose ReimburseCharge id equals the staged one; never act on a value not read
    live. The dbo step runs FIRST; any error in it skips the staging CAS (the line stays a candidate and is
    retried next run).
  - A dbo source line still used by ANY dbo.InvoiceLineItem is NOT un-billed: one deduped
    [qbo].[ReconciliationIssue] (drift_type charge_uninvoiced_locally_billed) is recorded instead and staging
    is NOT flipped: the line stays a candidate (HasBeenBilled + un-invoiced charge), so once the local invoice
    line is gone a later run un-bills dbo and CASes. At most max_unbill_per_run (default 50) dbo un-bills per
    entity per run; the rest are deferred (no dbo write, no CAS).
  - A dbo line not found by QBO identity (unmapped) logs a WARNING and staging is CAS'd (nothing local to un-bill).
  - Dry run performs no writes of any kind (no dbo, no issue, no CAS).
  - Never deletes staging or dbo rows; QBO is read-only (no outbox / no push).

External writes:
  - None (live QBO GET/query only).

Sprocs used:
  - ReadQboBillLinesByBillableStatusAndRealmId / UpdateQboBillLineBillableStatusById
  - ReadQboPurchaseLinesByBillableStatusAndRealmId / UpdateQboPurchaseLineBillableStatusById
  - ReadQboVendorCreditLinesByBillableStatusAndRealmId / UpdateQboVendorCreditLineBillableStatusById
  - ReadQboBillLinesWithUninvoicedChargeByRealmId
  - ReadQboPurchaseLinesWithUninvoicedChargeByRealmId
  - ReadQboVendorCreditLinesWithUninvoicedChargeByRealmId
  - ReadInvoiceLineItemIdsBySourceLineItem (via InvoiceLineItemService)
"""
# Python Standard Library Imports
import logging
from dataclasses import dataclass, fields
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

# Local Imports
from integrations.intuit.qbo.base.delete_reconcile import SYSTEMIC_QBO_ERRORS
from integrations.intuit.qbo.base.charge_link import reimburse_charge_id_from_linked_txn
from integrations.intuit.qbo.base.drift_types import DRIFT_CHARGE_UNINVOICED_LOCALLY_BILLED
from integrations.intuit.qbo.base.ids import normalize_qbo_id
from integrations.intuit.qbo.billable_refresh.business.line_utils import extract_billable_status
from integrations.intuit.qbo.billable_refresh.business.staged import StagedBillableQboLine

logger = logging.getLogger(__name__)

_HEADER_ID_BATCH_SIZE = 100
_BILLABLE = "Billable"
_HAS_BEEN_BILLED = "HasBeenBilled"


class _LineRepo(Protocol):
    def read_by_billable_status_and_realm_id(
        self, realm_id: str, billable_status: str = _BILLABLE
    ) -> Sequence[StagedBillableQboLine]: ...

    def update_billable_status_by_id(
        self,
        *,
        id: int,
        expected_billable_status: str,
        billable_status: str,
    ) -> bool: ...

    def read_with_uninvoiced_charge_by_realm_id(
        self, realm_id: str
    ) -> Sequence[StagedBillableQboLine]: ...


def _counts_dict(counts: Any, *, dry_run: bool, dry_only: tuple, live_only: tuple) -> dict:
    """Every counter in declaration order except the dry-only/live-only ones, then the mode's own, appended."""
    skip = set(dry_only) | set(live_only)
    out = {f.name: getattr(counts, f.name) for f in fields(counts) if f.name not in skip}
    for name in dry_only if dry_run else live_only:
        out[name] = getattr(counts, name)
    return out


# Reverse-pass dbo outcome -> (counter attribute, may the staging CAS follow?). A flagged or cap-deferred line stays
# a candidate: flagged lines self-heal once the local invoice line is gone; capped lines are retried next run.
_UNBILL_OUTCOMES: Dict[str, tuple] = {
    "unbilled": ("dbo_unbilled", True),
    "would_unbill": ("dbo_would_unbill", True),
    "already_unbilled": ("dbo_already_unbilled", True),
    "unmapped": ("dbo_unmapped", True),
    "flagged": ("dbo_flagged", False),
    "would_flag": ("dbo_would_flag", False),
    "flag_deduped": ("dbo_flag_deduped", False),
    "deferred_by_cap": ("deferred_by_cap", False),
}


class _IssueKeyCache:
    """Unresolved charge_uninvoiced_locally_billed keys for ONE entity's reverse pass; loaded lazily on the
    first flag. A failed read is not cached (the next flag retries)."""

    def __init__(self) -> None:
        self.keys: Optional[set] = None

    def load(self, repo: Any) -> set:
        if self.keys is None:
            self.keys = {
                (r, e, normalize_qbo_id(q))
                for r, e, q in repo.read_unresolved_issue_keys_by_drift_type(
                    DRIFT_CHARGE_UNINVOICED_LOCALLY_BILLED
                )
                if normalize_qbo_id(q)
            }
        return self.keys


@dataclass
class _ReverseCounts:
    candidate_lines: int = 0
    headers_fetched: int = 0
    headers_missing_live: int = 0
    lines_missing_live: int = 0
    reverse_still_billed: int = 0
    reverse_charge_changed: int = 0
    reverse_other_status: int = 0
    dbo_unbilled: int = 0
    dbo_would_unbill: int = 0
    dbo_flagged: int = 0
    dbo_would_flag: int = 0
    dbo_flag_deduped: int = 0
    dbo_already_unbilled: int = 0
    dbo_unmapped: int = 0
    dbo_unbill_errors: int = 0
    flipped_to_billable: int = 0
    cas_skipped: int = 0
    cas_errors: int = 0
    deferred_by_cap: int = 0
    batch_errors: int = 0

    def as_dict(self, *, dry_run: bool) -> dict:
        return _counts_dict(
            self,
            dry_run=dry_run,
            dry_only=("dbo_would_unbill", "dbo_would_flag"),
            live_only=("dbo_unbilled", "dbo_flagged"),
        )


@dataclass
class _EntityCounts:
    staged_billable_lines: int = 0
    headers_fetched: int = 0
    headers_missing_live: int = 0
    lines_missing_live: int = 0
    flipped_to_has_been_billed: int = 0
    flipped_to_not_billable: int = 0
    cas_skipped: int = 0
    dbo_healed: int = 0
    dbo_would_heal: int = 0
    dbo_heal_errors: int = 0
    batch_errors: int = 0
    unexpected_live_status: int = 0

    def as_dict(self, *, dry_run: bool) -> dict:
        return _counts_dict(self, dry_run=dry_run, dry_only=("dbo_would_heal",), live_only=("dbo_healed",))


@dataclass
class _EntityConfig:
    name: str
    line_repo: _LineRepo
    query_by_ids: Callable[[List[str]], List[Any]]
    unbill_dbo: Callable[..., str]
    heal_dbo: Optional[Callable[..., bool]] = None


class QboBillableStatusRefreshService:
    """Re-fetch QBO headers for staged-Billable lines and CAS-correct BillableStatus."""

    def __init__(
        self,
        *,
        bill_line_repo: Optional[_LineRepo] = None,
        purchase_line_repo: Optional[_LineRepo] = None,
        vendor_credit_line_repo: Optional[_LineRepo] = None,
        bill_client: Optional[Any] = None,
        purchase_client: Optional[Any] = None,
        vendor_credit_client: Optional[Any] = None,
        bill_service_factory: Optional[Callable[[], Any]] = None,
        bill_line_item_service_factory: Optional[Callable[[], Any]] = None,
        expense_service_factory: Optional[Callable[[], Any]] = None,
        expense_line_item_service_factory: Optional[Callable[[], Any]] = None,
        bill_credit_service_factory: Optional[Callable[[], Any]] = None,
        bill_credit_line_item_service_factory: Optional[Callable[[], Any]] = None,
        invoice_line_item_service_factory: Optional[Callable[[], Any]] = None,
        reconciliation_repo_factory: Optional[Callable[[], Any]] = None,
        max_unbill_per_run: int = 50,
    ):
        self._max_unbill_per_run = max_unbill_per_run
        if bill_line_repo is None:
            from integrations.intuit.qbo.bill.persistence.repo import QboBillLineRepository

            bill_line_repo = QboBillLineRepository()
        if purchase_line_repo is None:
            from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseLineRepository

            purchase_line_repo = QboPurchaseLineRepository()
        if vendor_credit_line_repo is None:
            from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository

            vendor_credit_line_repo = QboVendorCreditRepository()

        self._bill_line_repo = bill_line_repo
        self._purchase_line_repo = purchase_line_repo
        self._vendor_credit_line_repo = vendor_credit_line_repo

        self._bill_client = bill_client
        self._purchase_client = purchase_client
        self._vendor_credit_client = vendor_credit_client

        self._bill_service_factory = bill_service_factory or self._bill_service
        self._bill_line_item_service_factory = (
            bill_line_item_service_factory or self._bill_line_item_service
        )
        self._expense_service_factory = expense_service_factory or self._expense_service
        self._expense_line_item_service_factory = (
            expense_line_item_service_factory or self._expense_line_item_service
        )
        self._bill_credit_service_factory = bill_credit_service_factory or self._bill_credit_service
        self._bill_credit_line_item_service_factory = (
            bill_credit_line_item_service_factory or self._bill_credit_line_item_service
        )
        self._invoice_line_item_service_factory = (
            invoice_line_item_service_factory or self._invoice_line_item_service
        )
        self._reconciliation_repo_factory = reconciliation_repo_factory or self._reconciliation_repo

    @staticmethod
    def _bill_service():
        from entities.bill.business.service import BillService

        return BillService()

    @staticmethod
    def _bill_line_item_service():
        from entities.bill_line_item.business.service import BillLineItemService

        return BillLineItemService()

    @staticmethod
    def _expense_service():
        from entities.expense.business.service import ExpenseService

        return ExpenseService()

    @staticmethod
    def _expense_line_item_service():
        from entities.expense_line_item.business.service import ExpenseLineItemService

        return ExpenseLineItemService()

    @staticmethod
    def _bill_credit_service():
        from entities.bill_credit.business.service import BillCreditService

        return BillCreditService()

    @staticmethod
    def _bill_credit_line_item_service():
        from entities.bill_credit_line_item.business.service import BillCreditLineItemService

        return BillCreditLineItemService()

    @staticmethod
    def _invoice_line_item_service():
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        return InvoiceLineItemService()

    @staticmethod
    def _reconciliation_repo():
        from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

        return ReconciliationIssueRepository()

    def run(self, realm_id: str, *, dry_run: bool = False) -> dict:
        result: Dict[str, Any] = {"dry_run": dry_run, "aborted": None}
        entities = self._entity_configs()
        for cfg in entities:
            counts, aborted = self._run_entity(
                realm_id=realm_id,
                cfg=cfg,
                dry_run=dry_run,
            )
            result[cfg.name] = counts.as_dict(dry_run=dry_run)
            if aborted:
                result["aborted"] = aborted
                break
            reverse_counts, aborted = self._run_reverse_entity(
                realm_id=realm_id,
                cfg=cfg,
                dry_run=dry_run,
            )
            result[cfg.name]["reverse"] = reverse_counts.as_dict(dry_run=dry_run)
            if aborted:
                result["aborted"] = aborted
                break
        return result

    def _entity_configs(self) -> List[_EntityConfig]:
        return [
            _EntityConfig(
                name="bill",
                line_repo=self._bill_line_repo,
                query_by_ids=self._query_bills_by_ids,
                heal_dbo=self._try_dbo_heal_bill,
                unbill_dbo=self._try_dbo_unbill_bill,
            ),
            _EntityConfig(
                name="purchase",
                line_repo=self._purchase_line_repo,
                query_by_ids=self._query_purchases_by_ids,
                heal_dbo=self._try_dbo_heal_purchase,
                unbill_dbo=self._try_dbo_unbill_purchase,
            ),
            _EntityConfig(
                name="vendor_credit",
                line_repo=self._vendor_credit_line_repo,
                query_by_ids=self._query_vendor_credits_by_ids,
                heal_dbo=self._try_dbo_heal_vendor_credit,
                unbill_dbo=self._try_dbo_unbill_vendor_credit,
            ),
        ]

    def _query_by_ids(self, ids: List[str], *, entity: str, client: Any, method_name: str) -> List[Any]:
        if client is None:
            raise RuntimeError(f"{entity}_client required")
        return getattr(client, method_name)(ids)

    def _query_bills_by_ids(self, ids: List[str]) -> List[Any]:
        return self._query_by_ids(
            ids,
            entity="bill",
            client=self._bill_client,
            method_name="query_bills_by_ids",
        )

    def _query_purchases_by_ids(self, ids: List[str]) -> List[Any]:
        return self._query_by_ids(
            ids,
            entity="purchase",
            client=self._purchase_client,
            method_name="query_purchases_by_ids",
        )

    def _query_vendor_credits_by_ids(self, ids: List[str]) -> List[Any]:
        return self._query_by_ids(
            ids,
            entity="vendor_credit",
            client=self._vendor_credit_client,
            method_name="query_vendor_credits_by_ids",
        )

    def _run_entity(
        self,
        *,
        realm_id: str,
        cfg: _EntityConfig,
        dry_run: bool,
    ) -> tuple[_EntityCounts, Optional[str]]:
        counts = _EntityCounts()
        try:
            staged_lines = list(
                cfg.line_repo.read_by_billable_status_and_realm_id(
                    realm_id, billable_status=_BILLABLE
                )
            )
        except Exception:
            logger.exception(
                "billable_refresh.%s.read_staged_failed realm=%s", cfg.name, realm_id
            )
            counts.batch_errors += 1
            return counts, None

        counts.staged_billable_lines = len(staged_lines)
        if not staged_lines:
            return counts, None

        by_header = self._group_by_header(staged_lines, counts)

        live_by_id, aborted = self._fetch_live_headers(cfg, sorted(by_header.keys()), counts)
        if aborted:
            return counts, aborted

        for header_id, staged, qbo_line_id, live_line in self._iter_live_lines(by_header, live_by_id, counts):
            live_status = extract_billable_status(live_line)
            if live_status == _BILLABLE:
                continue

            if live_status not in (_HAS_BEEN_BILLED, "NotBillable"):
                counts.unexpected_live_status += 1
                logger.warning(
                    "billable_refresh.%s.unexpected_live_status line_id=%s status=%r",
                    cfg.name,
                    staged.id,
                    live_status,
                )
                continue

            if live_status == _HAS_BEEN_BILLED:
                counts.flipped_to_has_been_billed += 1
            else:
                counts.flipped_to_not_billable += 1

            if live_status == _HAS_BEEN_BILLED and cfg.heal_dbo is not None:
                try:
                    healed = cfg.heal_dbo(
                        realm_id=realm_id,
                        header_qbo_id=header_id,
                        qbo_line_id=qbo_line_id,
                        dry_run=dry_run,
                    )
                except Exception:
                    if dry_run:
                        logger.exception(
                            "billable_refresh.%s.dbo_heal_dry_run_failed header=%s line=%s",
                            cfg.name,
                            header_id,
                            qbo_line_id,
                        )
                    else:
                        logger.exception(
                            "billable_refresh.%s.dbo_heal_failed header=%s line=%s",
                            cfg.name,
                            header_id,
                            qbo_line_id,
                        )
                    counts.dbo_heal_errors += 1
                    if not dry_run:
                        continue
                else:
                    if healed:
                        if dry_run:
                            counts.dbo_would_heal += 1
                        else:
                            counts.dbo_healed += 1

            if dry_run:
                continue

            try:
                updated = cfg.line_repo.update_billable_status_by_id(
                    id=staged.id,
                    expected_billable_status=_BILLABLE,
                    billable_status=live_status,
                )
            except Exception:
                logger.exception(
                    "billable_refresh.%s.cas_failed staged_id=%s",
                    cfg.name,
                    staged.id,
                )
                continue

            if not updated:
                counts.cas_skipped += 1

        return counts, None

    def _fetch_live_headers(
        self, cfg: _EntityConfig, header_ids: List[str], counts: Any
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Batched live header fetch shared by both passes. Returns (live_by_id, aborted_reason).

        Increments counts.headers_fetched / counts.batch_errors; a SYSTEMIC_QBO_ERRORS aborts."""
        live_by_id: Dict[str, Any] = {}
        for offset in range(0, len(header_ids), _HEADER_ID_BATCH_SIZE):
            batch = header_ids[offset : offset + _HEADER_ID_BATCH_SIZE]
            try:
                live_headers = cfg.query_by_ids(batch)
            except SYSTEMIC_QBO_ERRORS as error:
                reason = f"{type(error).__name__}: {error}"
                logger.warning(
                    "billable_refresh.%s.systemic_abort batch=%s reason=%s",
                    cfg.name,
                    batch[:3],
                    reason,
                )
                return live_by_id, type(error).__name__
            except Exception:
                logger.exception(
                    "billable_refresh.%s.batch_fetch_failed batch_size=%s",
                    cfg.name,
                    len(batch),
                )
                counts.batch_errors += 1
                continue

            counts.headers_fetched += len(live_headers)
            for header in live_headers:
                hid = normalize_qbo_id(getattr(header, "id", None))
                if hid:
                    live_by_id[hid] = header
        return live_by_id, None

    @staticmethod
    def _live_line_index(live_header: Any) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for ll in getattr(live_header, "line", None) or []:
            lid = normalize_qbo_id(getattr(ll, "id", None))
            if lid:
                out[lid] = ll
        return out

    @staticmethod
    def _group_by_header(rows: Sequence[StagedBillableQboLine], counts: Any) -> Dict[str, List[StagedBillableQboLine]]:
        by_header: Dict[str, List[StagedBillableQboLine]] = {}
        for row in rows:
            header_id = normalize_qbo_id(row.header_qbo_id)
            if not header_id:
                counts.headers_missing_live += 1
                continue
            by_header.setdefault(header_id, []).append(row)
        return by_header

    @classmethod
    def _iter_live_lines(cls, by_header: Dict[str, List[StagedBillableQboLine]], live_by_id: Dict[str, Any], counts: Any):
        """Yield (header_id, staged, qbo_line_id, live_line) for every staged line whose live header and line exist,
        counting headers_missing_live / lines_missing_live for the rest."""
        for header_id, lines in by_header.items():
            live_header = live_by_id.get(header_id)
            if live_header is None:
                counts.headers_missing_live += len(lines)
                continue
            live_line_by_id = cls._live_line_index(live_header)
            for staged in lines:
                qbo_line_id = normalize_qbo_id(staged.qbo_line_id)
                if not qbo_line_id or qbo_line_id not in live_line_by_id:
                    counts.lines_missing_live += 1
                    continue
                yield header_id, staged, qbo_line_id, live_line_by_id[qbo_line_id]

    def _run_reverse_entity(
        self,
        *,
        realm_id: str,
        cfg: _EntityConfig,
        dry_run: bool,
    ) -> tuple[_ReverseCounts, Optional[str]]:
        """U-778b reverse flip: staged HasBeenBilled + un-invoiced ReimburseCharge -> Billable."""
        counts = _ReverseCounts()
        try:
            candidates = list(cfg.line_repo.read_with_uninvoiced_charge_by_realm_id(realm_id))
        except Exception:
            logger.exception("billable_refresh.%s.reverse_read_failed realm=%s", cfg.name, realm_id)
            counts.batch_errors += 1
            return counts, None

        counts.candidate_lines = len(candidates)
        if not candidates:
            return counts, None

        by_header = self._group_by_header(candidates, counts)

        live_by_id, aborted = self._fetch_live_headers(cfg, sorted(by_header.keys()), counts)
        if aborted:
            return counts, aborted

        issue_keys = _IssueKeyCache()
        for header_id, staged, qbo_line_id, live_line in self._iter_live_lines(by_header, live_by_id, counts):
            live_status = extract_billable_status(live_line)
            if live_status == _HAS_BEEN_BILLED:
                counts.reverse_still_billed += 1
                continue
            if live_status != _BILLABLE:
                counts.reverse_other_status += 1
                continue
            staged_charge = normalize_qbo_id(staged.charge_qbo_id)
            live_charge = normalize_qbo_id(
                reimburse_charge_id_from_linked_txn(getattr(live_line, "linked_txn", None))
            )
            if not staged_charge or live_charge != staged_charge:
                counts.reverse_charge_changed += 1
                continue

            try:
                outcome = cfg.unbill_dbo(
                    realm_id=realm_id,
                    header_qbo_id=header_id,
                    qbo_line_id=qbo_line_id,
                    charge_qbo_id=staged_charge,
                    dry_run=dry_run,
                    issue_keys=issue_keys,
                    allow_unbill=(counts.dbo_unbilled + counts.dbo_would_unbill) < self._max_unbill_per_run,
                )
            except Exception:
                logger.exception(
                    "billable_refresh.%s.dbo_unbill_failed header=%s line=%s",
                    cfg.name,
                    header_id,
                    qbo_line_id,
                )
                counts.dbo_unbill_errors += 1
                continue
            counter, cas_allowed = _UNBILL_OUTCOMES[outcome]
            setattr(counts, counter, getattr(counts, counter) + 1)
            if outcome == "unmapped":
                logger.warning(
                    "billable_refresh.%s.reverse_unmapped header=%s line=%s charge=%s realm=%s",
                    cfg.name,
                    header_id,
                    qbo_line_id,
                    staged_charge,
                    realm_id,
                )
            if not cas_allowed or dry_run:
                continue

            try:
                updated = cfg.line_repo.update_billable_status_by_id(
                    id=staged.id,
                    expected_billable_status=_HAS_BEEN_BILLED,
                    billable_status=_BILLABLE,
                )
            except Exception:
                logger.exception(
                    "billable_refresh.%s.reverse_cas_failed staged_id=%s", cfg.name, staged.id
                )
                counts.cas_errors += 1
                continue
            if updated:
                counts.flipped_to_billable += 1
            else:
                counts.cas_skipped += 1

        return counts, None

    def _try_dbo_unbill_parent_line(
        self,
        *,
        realm_id: str,
        header_qbo_id: str,
        qbo_line_id: str,
        charge_qbo_id: str,
        dry_run: bool,
        issue_keys: _IssueKeyCache,
        allow_unbill: bool,
        entity: str,
        source_type: str,
        header_service_factory: Callable[[], Any],
        line_item_service_factory: Callable[[], Any],
        parent_public_id_kw: str,
        via_internal_pipeline: bool,
    ) -> str:
        """Guarded dbo IsBilled 1->0. Returns an outcome token; raises on any failure."""
        header_svc = header_service_factory()
        parent = header_svc.read_by_qbo_identity(header_qbo_id, realm_id)
        if not parent:
            return "unmapped"
        line_svc = line_item_service_factory()
        source = line_svc.read_by_qbo_identity(parent.id, qbo_line_id)
        if not source:
            return "unmapped"
        if not source.is_billed:
            return "already_unbilled"

        invoice_lines = list(
            self._invoice_line_item_service_factory().read_ids_by_source_line_item(source_type, source.id)
        )
        if invoice_lines:
            return self._flag_locally_billed(
                realm_id=realm_id,
                header_qbo_id=header_qbo_id,
                qbo_line_id=qbo_line_id,
                charge_qbo_id=charge_qbo_id,
                dry_run=dry_run,
                issue_keys=issue_keys,
                source_type=source_type,
                source_public_id=source.public_id,
                invoice_lines=invoice_lines,
            )

        if not allow_unbill:
            return "deferred_by_cap"
        if dry_run:
            return "would_unbill"
        kwargs: Dict[str, Any] = {parent_public_id_kw: parent.public_id, "is_billed": False}
        if via_internal_pipeline:
            kwargs["_via_internal_pipeline"] = True
        line_svc.update_by_public_id(
            public_id=source.public_id,
            row_version=source.row_version,
            **kwargs,
        )
        logger.info(
            "billable_refresh.dbo_unbilled",
            extra={
                "entity": entity,
                "parent_public_id": str(parent.public_id),
                "line_public_id": str(source.public_id),
                "header_qbo_id": header_qbo_id,
                "line_qbo_id": qbo_line_id,
                "charge_qbo_id": charge_qbo_id,
                "realm_id": realm_id,
            },
        )
        return "unbilled"

    def _flag_locally_billed(
        self,
        *,
        realm_id: str,
        header_qbo_id: str,
        qbo_line_id: str,
        charge_qbo_id: str,
        dry_run: bool,
        issue_keys: _IssueKeyCache,
        source_type: str,
        source_public_id: Any,
        invoice_lines: List[tuple],
    ) -> str:
        reconciliation_repo = self._reconciliation_repo_factory()
        keys = issue_keys.load(reconciliation_repo)
        qbo_id = f"{header_qbo_id}:{qbo_line_id}"
        key = (realm_id, source_type, normalize_qbo_id(qbo_id))
        if key in keys:
            return "flag_deduped"
        if dry_run:
            return "would_flag"
        details = (
            f"charge_qbo_id={charge_qbo_id}; local invoice line ids="
            f"{[i for i, _ in invoice_lines]}, invoice ids={sorted({inv for _, inv in invoice_lines})}; "
            "QBO un-invoiced this charge; a local invoice line still uses it — dbo IsBilled left untouched"
        )
        reconciliation_repo.create(
            drift_type=DRIFT_CHARGE_UNINVOICED_LOCALLY_BILLED,
            severity="high",
            action="flagged",
            entity_type=source_type,
            realm_id=realm_id,
            qbo_id=qbo_id,
            entity_public_id=source_public_id,
            details=details,
        )
        keys.add(key)
        return "flagged"

    def _try_dbo_unbill_bill(self, **kw) -> str:
        return self._try_dbo_unbill_parent_line(
            entity="bill",
            source_type="BillLineItem",
            header_service_factory=self._bill_service_factory,
            line_item_service_factory=self._bill_line_item_service_factory,
            parent_public_id_kw="bill_public_id",
            via_internal_pipeline=True,
            **kw,
        )

    def _try_dbo_unbill_purchase(self, **kw) -> str:
        return self._try_dbo_unbill_parent_line(
            entity="purchase",
            source_type="ExpenseLineItem",
            header_service_factory=self._expense_service_factory,
            line_item_service_factory=self._expense_line_item_service_factory,
            parent_public_id_kw="expense_public_id",
            via_internal_pipeline=True,
            **kw,
        )

    def _try_dbo_unbill_vendor_credit(self, **kw) -> str:
        # BillCreditLineItemService.update_by_public_id does not accept _via_internal_pipeline.
        return self._try_dbo_unbill_parent_line(
            entity="vendor_credit",
            source_type="BillCreditLineItem",
            header_service_factory=self._bill_credit_service_factory,
            line_item_service_factory=self._bill_credit_line_item_service_factory,
            parent_public_id_kw="bill_credit_public_id",
            via_internal_pipeline=False,
            **kw,
        )

    def _try_dbo_heal_parent_line(
        self,
        *,
        realm_id: str,
        header_qbo_id: str,
        qbo_line_id: str,
        dry_run: bool,
        entity: str,
        header_service_factory: Callable[[], Any],
        line_item_service_factory: Callable[[], Any],
        parent_public_id_kw: str,
        via_internal_pipeline: bool = True,
    ) -> bool:
        header_svc = header_service_factory()
        parent = header_svc.read_by_qbo_identity(header_qbo_id, realm_id)
        if not parent:
            return False
        line_svc = line_item_service_factory()
        source = line_svc.read_by_qbo_identity(parent.id, qbo_line_id)
        if not source or source.is_billed:
            return False
        if dry_run:
            return True
        heal_kwargs: Dict[str, Any] = {parent_public_id_kw: parent.public_id, "is_billed": True}
        if via_internal_pipeline:
            heal_kwargs["_via_internal_pipeline"] = True
        line_svc.update_by_public_id(
            public_id=source.public_id,
            row_version=source.row_version,
            **heal_kwargs,
        )
        logger.info(
            "billable_refresh.dbo_healed",
            extra={
                "entity": entity,
                "parent_public_id": str(parent.public_id),
                "line_public_id": str(source.public_id),
                "header_qbo_id": header_qbo_id,
                "line_qbo_id": qbo_line_id,
                "realm_id": realm_id,
            },
        )
        return True

    def _try_dbo_heal_bill(
        self,
        *,
        realm_id: str,
        header_qbo_id: str,
        qbo_line_id: str,
        dry_run: bool = False,
    ) -> bool:
        return self._try_dbo_heal_parent_line(
            realm_id=realm_id,
            header_qbo_id=header_qbo_id,
            qbo_line_id=qbo_line_id,
            dry_run=dry_run,
            entity="bill",
            header_service_factory=self._bill_service_factory,
            line_item_service_factory=self._bill_line_item_service_factory,
            parent_public_id_kw="bill_public_id",
        )

    def _try_dbo_heal_purchase(
        self,
        *,
        realm_id: str,
        header_qbo_id: str,
        qbo_line_id: str,
        dry_run: bool = False,
    ) -> bool:
        return self._try_dbo_heal_parent_line(
            realm_id=realm_id,
            header_qbo_id=header_qbo_id,
            qbo_line_id=qbo_line_id,
            dry_run=dry_run,
            entity="purchase",
            header_service_factory=self._expense_service_factory,
            line_item_service_factory=self._expense_line_item_service_factory,
            parent_public_id_kw="expense_public_id",
        )

    def _try_dbo_heal_vendor_credit(
        self,
        *,
        realm_id: str,
        header_qbo_id: str,
        qbo_line_id: str,
        dry_run: bool = False,
    ) -> bool:
        # BillCreditLineItemService.update_by_public_id does not accept _via_internal_pipeline.
        return self._try_dbo_heal_parent_line(
            realm_id=realm_id,
            header_qbo_id=header_qbo_id,
            qbo_line_id=qbo_line_id,
            dry_run=dry_run,
            entity="vendor_credit",
            header_service_factory=self._bill_credit_service_factory,
            line_item_service_factory=self._bill_credit_line_item_service_factory,
            parent_public_id_kw="bill_credit_public_id",
            via_internal_pipeline=False,
        )
