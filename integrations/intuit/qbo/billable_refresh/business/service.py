"""
Billable-status refresh sweep for QBO Bill / Purchase / VendorCredit staging lines.

Owns:
  - Detecting staged qbo.*Line rows still marked Billable whose live QBO line differs.
  - Compare-and-set updates to qbo.*Line.BillableStatus only.
  - dbo BillLineItem / ExpenseLineItem IsBilled 0→1 heal when live is HasBeenBilled.

Invariants:
  - Staging writes touch BillableStatus (+ ModifiedDatetime) only via CAS from Billable.
  - dbo heal sets is_billed True only; never False; never other dbo fields.
  - Never deletes staging or dbo rows; QBO is read-only (no outbox / no push).

External writes:
  - None (live QBO GET/query only).

Sprocs used:
  - ReadQboBillLinesByBillableStatusAndRealmId / UpdateQboBillLineBillableStatusById
  - ReadQboPurchaseLinesByBillableStatusAndRealmId / UpdateQboPurchaseLineBillableStatusById
  - ReadQboVendorCreditLinesByBillableStatusAndRealmId / UpdateQboVendorCreditLineBillableStatusById
"""
# Python Standard Library Imports
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

# Local Imports
from integrations.intuit.qbo.base.delete_reconcile import SYSTEMIC_QBO_ERRORS
from integrations.intuit.qbo.base.ids import normalize_qbo_id
from integrations.intuit.qbo.billable_refresh.business.line_utils import extract_billable_status
from integrations.intuit.qbo.billable_refresh.business.staged import StagedBillableQboLine

logger = logging.getLogger(__name__)

_HEADER_ID_BATCH_SIZE = 100
_STAGED_BILLABLE = "Billable"
_LIVE_HAS_BEEN_BILLED = "HasBeenBilled"


class _LineRepo(Protocol):
    def read_by_billable_status_and_realm_id(
        self, realm_id: str, billable_status: str = _STAGED_BILLABLE
    ) -> Sequence[StagedBillableQboLine]: ...

    def update_billable_status_by_id(
        self,
        *,
        id: int,
        expected_billable_status: str,
        billable_status: str,
    ) -> bool: ...


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

    def as_dict(self, *, dry_run: bool) -> dict:
        out = {
            "staged_billable_lines": self.staged_billable_lines,
            "headers_fetched": self.headers_fetched,
            "headers_missing_live": self.headers_missing_live,
            "lines_missing_live": self.lines_missing_live,
            "flipped_to_has_been_billed": self.flipped_to_has_been_billed,
            "flipped_to_not_billable": self.flipped_to_not_billable,
            "cas_skipped": self.cas_skipped,
            "dbo_heal_errors": self.dbo_heal_errors,
            "batch_errors": self.batch_errors,
        }
        if dry_run:
            out["dbo_would_heal"] = self.dbo_would_heal
        else:
            out["dbo_healed"] = self.dbo_healed
        return out


@dataclass
class _EntityConfig:
    name: str
    line_repo: _LineRepo
    query_by_ids: Callable[[List[str]], List[Any]]
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
    ):
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

        self._bill_service_factory = bill_service_factory or (
            lambda: __import__(
                "entities.bill.business.service", fromlist=["BillService"]
            ).BillService()
        )
        self._bill_line_item_service_factory = bill_line_item_service_factory or (
            lambda: __import__(
                "entities.bill_line_item.business.service", fromlist=["BillLineItemService"]
            ).BillLineItemService()
        )
        self._expense_service_factory = expense_service_factory or (
            lambda: __import__(
                "entities.expense.business.service", fromlist=["ExpenseService"]
            ).ExpenseService()
        )
        self._expense_line_item_service_factory = expense_line_item_service_factory or (
            lambda: __import__(
                "entities.expense_line_item.business.service",
                fromlist=["ExpenseLineItemService"],
            ).ExpenseLineItemService()
        )

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
        return result

    def _entity_configs(self) -> List[_EntityConfig]:
        return [
            _EntityConfig(
                name="bill",
                line_repo=self._bill_line_repo,
                query_by_ids=self._query_bills_by_ids,
                heal_dbo=self._try_dbo_heal_bill,
            ),
            _EntityConfig(
                name="purchase",
                line_repo=self._purchase_line_repo,
                query_by_ids=self._query_purchases_by_ids,
                heal_dbo=self._try_dbo_heal_purchase,
            ),
            _EntityConfig(
                name="vendor_credit",
                line_repo=self._vendor_credit_line_repo,
                query_by_ids=self._query_vendor_credits_by_ids,
                heal_dbo=None,
            ),
        ]

    def _query_bills_by_ids(self, ids: List[str]) -> List[Any]:
        if self._bill_client is None:
            raise RuntimeError("bill_client required")
        return self._bill_client.query_bills_by_ids(ids)

    def _query_purchases_by_ids(self, ids: List[str]) -> List[Any]:
        if self._purchase_client is None:
            raise RuntimeError("purchase_client required")
        return self._purchase_client.query_purchases_by_ids(ids)

    def _query_vendor_credits_by_ids(self, ids: List[str]) -> List[Any]:
        if self._vendor_credit_client is None:
            raise RuntimeError("vendor_credit_client required")
        return self._vendor_credit_client.query_vendor_credits_by_ids(ids)

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
                    realm_id, billable_status=_STAGED_BILLABLE
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

        by_header: Dict[str, List[StagedBillableQboLine]] = {}
        for row in staged_lines:
            header_id = normalize_qbo_id(row.header_qbo_id)
            if not header_id:
                counts.headers_missing_live += 1
                continue
            by_header.setdefault(header_id, []).append(row)

        header_ids = sorted(by_header.keys())
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
                return counts, reason
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

        for header_id, lines in by_header.items():
            live_header = live_by_id.get(header_id)
            if live_header is None:
                counts.headers_missing_live += len(lines)
                continue

            live_lines = getattr(live_header, "line", None) or []
            live_line_by_id: Dict[str, Any] = {}
            for ll in live_lines:
                lid = normalize_qbo_id(getattr(ll, "id", None))
                if lid:
                    live_line_by_id[lid] = ll

            for staged in lines:
                qbo_line_id = normalize_qbo_id(staged.qbo_line_id)
                if not qbo_line_id or qbo_line_id not in live_line_by_id:
                    counts.lines_missing_live += 1
                    continue

                live_line = live_line_by_id[qbo_line_id]
                live_status = extract_billable_status(live_line)
                if live_status is None or live_status == _STAGED_BILLABLE:
                    continue

                if live_status == _LIVE_HAS_BEEN_BILLED:
                    counts.flipped_to_has_been_billed += 1
                elif live_status == "NotBillable":
                    counts.flipped_to_not_billable += 1
                else:
                    logger.info(
                        "billable_refresh.%s.unexpected_live_status line_id=%s status=%s",
                        cfg.name,
                        staged.id,
                        live_status,
                    )

                updated = False
                if not dry_run:
                    try:
                        updated = cfg.line_repo.update_billable_status_by_id(
                            id=staged.id,
                            expected_billable_status=_STAGED_BILLABLE,
                            billable_status=live_status,
                        )
                    except Exception:
                        logger.exception(
                            "billable_refresh.%s.cas_failed staged_id=%s",
                            cfg.name,
                            staged.id,
                        )
                        continue
                else:
                    updated = True

                if not dry_run and not updated:
                    counts.cas_skipped += 1
                    continue

                if live_status == _LIVE_HAS_BEEN_BILLED and cfg.heal_dbo is not None:
                    if dry_run:
                        counts.dbo_would_heal += 1
                    else:
                        try:
                            if cfg.heal_dbo(
                                realm_id=realm_id,
                                header_qbo_id=header_id,
                                qbo_line_id=qbo_line_id,
                            ):
                                counts.dbo_healed += 1
                        except Exception:
                            logger.exception(
                                "billable_refresh.%s.dbo_heal_failed header=%s line=%s",
                                cfg.name,
                                header_id,
                                qbo_line_id,
                            )
                            counts.dbo_heal_errors += 1

        return counts, None

    def _try_dbo_heal_bill(
        self, *, realm_id: str, header_qbo_id: str, qbo_line_id: str
    ) -> bool:
        bill_svc = self._bill_service_factory()
        bill = bill_svc.read_by_qbo_identity(header_qbo_id, realm_id)
        if not bill:
            return False
        bli_svc = self._bill_line_item_service_factory()
        source = bli_svc.read_by_qbo_identity(bill.id, qbo_line_id)
        if not source or source.is_billed:
            return False
        bli_svc.update_by_public_id(
            public_id=source.public_id,
            row_version=source.row_version,
            bill_public_id=bill.public_id,
            is_billed=True,
            _via_internal_pipeline=True,
        )
        return True

    def _try_dbo_heal_purchase(
        self, *, realm_id: str, header_qbo_id: str, qbo_line_id: str
    ) -> bool:
        expense_svc = self._expense_service_factory()
        expense = expense_svc.read_by_qbo_identity(header_qbo_id, realm_id)
        if not expense:
            return False
        eli_svc = self._expense_line_item_service_factory()
        source = eli_svc.read_by_qbo_identity(expense.id, qbo_line_id)
        if not source or source.is_billed:
            return False
        eli_svc.update_by_public_id(
            public_id=source.public_id,
            row_version=source.row_version,
            expense_public_id=expense.public_id,
            is_billed=True,
            _via_internal_pipeline=True,
        )
        return True
