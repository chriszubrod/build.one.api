"""U-778d1 -- value objects for the QBO invoice-push dry run (design u778d sections 3-5).

Pure: frozen dataclasses only. No DB, HTTP, env. Bite 2 fills `SourceLineFacts` from dbo and
`LiveSourceLine` / `LiveCharge` from QBO; this module never reads either.
Money is `Decimal` everywhere (built with `Decimal(str(x))`, never float arithmetic).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Union

SUPPORTED_SOURCE_TYPES = ("Bill", "Expense", "BillCredit")
DIGITS = re.compile(r"[0-9]{1,50}")  # a QBO id: fullmatch only
CENT = Decimal("0.01")  # the money quantum (2 dp)
NON_TAX = "NON"  # the QBO TaxCode for "not taxable"
RC_TYPE = "ReimburseCharge"  # LinkedTxn.TxnType of a ReimburseCharge (== qbo.base.charge_link.REIMBURSE_CHARGE_TXN_TYPE)


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def dec(x: Any) -> Optional[Decimal]:
    """None-preserving Decimal(str(x)) -- the single float->Decimal conversion point for QBO JSON."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


class ChargeMalformed(ValueError):
    """A live ReimburseCharge lacks evidence the eligibility rules need (never defaulted -- see `LiveCharge.from_qbo`)."""


class InvoiceNotFound(LookupError):
    """The compared QBO invoice does not exist for the authorized project (also: belongs to another customer).
    Carries nothing about the invoice."""


@dataclass(frozen=True)
class MalformedCharge:
    """Stands in for a charge row that failed strict validation; eligibility turns it into `charge_malformed`."""
    id: str
    why: str


@dataclass(frozen=True)
class StagedInvoiceLink:
    """A staged qbo.InvoiceLine (realm-scoped) whose LinkedTxn is a ReimburseCharge."""
    charge_id: str
    invoice_qbo_id: str
    invoice_line_id: Optional[str] = None


def ref_value(ref: Any) -> Optional[str]:
    """A QBO reference object -> its `value` (names are response noise and are never compared)."""
    if isinstance(ref, Mapping):
        v = ref.get("value")
        return None if v is None else str(v)
    return None


@dataclass(frozen=True)
class FeeLine:
    sub_cost_code_id: Union[int, str]
    description: str
    qty: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class FeeItem:
    """Resolved QBO refs for one fee line (bite 2 resolves via resolve_qbo_item_ref)."""
    item_ref: str
    account_ref: Optional[str] = None


@dataclass(frozen=True)
class Proposal:
    project_id: Union[int, str]
    doc_number: str
    txn_date: str  # ISO calendar date
    due_date: Optional[str]  # ISO calendar date; REQUIRED (check_header: due_date_missing)
    payment_term_id: Optional[str]
    source_lines: tuple  # ((source_type, source_line_id), ...) in invoice order
    fee_lines: tuple = ()  # (FeeLine, ...)


@dataclass(frozen=True)
class ProposalContext:
    project_id: Union[int, str]
    customer_id: Optional[str]
    home_currency: str = "USD"
    doc_number_used_locally: bool = False
    doc_number_used_in_qbo: bool = False
    # compare mode: the QBO invoice being compared. Reasons that are CONSEQUENCES of that invoice existing are
    # tolerated only when the evidence points at exactly this invoice (see eligibility).
    compare_invoice_id: Optional[str] = None
    doc_number_local_qbo_ids: tuple = ()  # QboId (or None) of each dbo invoice in the project using the number
    doc_number_qbo_ids: tuple = ()  # Ids of the QBO invoices using the number

    def only_names_compared(self, ids) -> bool:
        """THE compare-mode tolerance predicate: evidence exists AND every piece of it names exactly the compared
        invoice (never in plain dry-run mode). It is applied to exactly seven reasons, each a consequence of the
        compared invoice existing: doc_number_used_locally, doc_number_used_in_qbo, locally_billed,
        already_on_invoice, charge_on_staged_invoice, charge_invoiced, and not_billable_in_qbo when the live
        BillableStatus is HasBeenBilled."""
        target = self.compare_invoice_id
        return bool(target) and bool(ids) and all(i is not None and str(i) == str(target) for i in ids)


@dataclass(frozen=True)
class SourceLineFacts:
    """What bite 2 reads from dbo for one source line."""
    source_type: str
    source_line_id: Union[int, str]
    project_id: Union[int, str]
    parent_is_draft: bool
    parent_deleted: bool
    is_billable: bool
    is_billed: bool
    referenced_by_ili_ids: tuple
    sub_cost_code_is_holding: bool
    amount: Optional[Decimal]
    markup: Optional[Decimal]
    price: Optional[Decimal]
    billable_amount: Optional[Decimal]  # BillCredit only
    parent_qbo_id: Optional[str]
    line_qbo_id: Optional[str]
    # EVERY distinct staged ReimburseChargeQboId for this line's staged identity (a warning-only cross-check: the
    # live link wins). Staged INVOICE evidence is looked up by the LIVE charge id, not from these.
    staged_charge_ids: tuple = ()
    # aligned with `referenced_by_ili_ids`: each referencing ILI's dbo invoice QboId in the realm (None = unmapped)
    ili_invoice_qbo_ids: tuple = ()


@dataclass(frozen=True)
class LiveSourceLine:
    billable_status: Optional[str]
    charge_id: Optional[str]  # from the source line's LinkedTxn (ReimburseCharge)


@dataclass(frozen=True)
class LiveChargeLine:
    line_id: str
    amount: Decimal
    description: Optional[str]
    item_ref: Optional[str]
    item_account_ref: Optional[str]
    tax_code_ref: Optional[str]
    qty: Optional[Decimal]
    unit_price: Optional[Decimal]
    markup_percent: Optional[Decimal]
    has_markup_info: bool = False  # PRESENCE of ReimburseLineDetail.MarkupInfo (a 0% markup is still present)


@dataclass(frozen=True)
class LiveCharge:
    id: str
    sync_token: Optional[str]
    has_been_invoiced: bool
    customer_id: Optional[str]
    currency: Optional[str]
    txn_date: Optional[str]
    lines: tuple  # (LiveChargeLine, ...)
    invoice_ids: tuple = ()  # header LinkedTxn entries of type Invoice: the invoice(s) that consumed this charge

    @classmethod
    def from_qbo(cls, rc: Mapping[str, Any]) -> "LiveCharge":
        """Strict: raises `ChargeMalformed` unless Id is a digit string, HasBeenInvoiced an explicit bool,
        CustomerRef.value present, >= 1 line, and every line has a digit-string Id and a numeric Amount.
        Nothing is defaulted (a missing flag must never read as False, a missing line id as "None")."""
        try:
            return cls._from_qbo(rc)
        except ChargeMalformed:
            raise
        except (InvalidOperation, ValueError, TypeError, AttributeError) as e:
            raise ChargeMalformed(f"unparseable charge: {e!r}") from e

    @classmethod
    def _from_qbo(cls, rc: Mapping[str, Any]) -> "LiveCharge":
        def bad(msg):
            raise ChargeMalformed(msg)
        if not isinstance(rc, Mapping):
            bad("charge is not an object")
        rid = rc.get("Id")
        if not isinstance(rid, (str, int)) or isinstance(rid, bool) or not DIGITS.fullmatch(str(rid)):
            bad(f"Id {rid!r} is not a digit string")
        if not isinstance(rc.get("HasBeenInvoiced"), bool):
            bad(f"HasBeenInvoiced {rc.get('HasBeenInvoiced')!r} is not an explicit boolean")
        if not ref_value(rc.get("CustomerRef")):
            bad("CustomerRef.value is missing")
        raw_lines = rc.get("Line")
        if not isinstance(raw_lines, list) or not raw_lines:
            bad("charge has no lines")
        lines = []
        for i, ln in enumerate(raw_lines):
            if not isinstance(ln, Mapping):
                bad(f"Line[{i}] is not an object")
            lid = ln.get("Id")
            if not isinstance(lid, (str, int)) or isinstance(lid, bool) or not DIGITS.fullmatch(str(lid)):
                bad(f"Line[{i}].Id {lid!r} is not a non-empty digit string")
            amt = ln.get("Amount")
            if isinstance(amt, bool) or not isinstance(amt, (int, float, Decimal)):
                bad(f"Line[{i}].Amount {amt!r} is not numeric")
            d = ln.get("ReimburseLineDetail") or {}
            mk = d.get("MarkupInfo")
            lines.append(LiveChargeLine(
                line_id=str(lid),
                amount=dec(amt),
                description=ln.get("Description"),
                item_ref=ref_value(d.get("ItemRef")),
                item_account_ref=ref_value(d.get("ItemAccountRef")),
                tax_code_ref=ref_value(d.get("TaxCodeRef")),
                qty=dec(d.get("Qty")),
                unit_price=dec(d.get("UnitPrice")),
                markup_percent=dec((mk or {}).get("Percent")),
                has_markup_info="MarkupInfo" in d,
            ))
        invoice_ids = tuple(str(lt.get("TxnId")) for lt in rc.get("LinkedTxn") or []
                            if isinstance(lt, Mapping) and lt.get("TxnType") == "Invoice")
        return cls(
            id=str(rid),
            sync_token=None if rc.get("SyncToken") is None else str(rc.get("SyncToken")),
            has_been_invoiced=rc["HasBeenInvoiced"],
            customer_id=ref_value(rc.get("CustomerRef")),
            currency=ref_value(rc.get("CurrencyRef")),
            txn_date=rc.get("TxnDate"),
            lines=tuple(lines),
            invoice_ids=invoice_ids,
        )


@dataclass(frozen=True)
class LineVerdict:
    source_type: str
    source_line_id: Union[int, str]
    ok: bool
    reasons: tuple = ()   # "<code>: <detail with both values>"
    warnings: tuple = ()  # same format; never block
    charge_id: Optional[str] = None


@dataclass(frozen=True)
class ProposalVerdict:
    ok: bool
    header_reasons: tuple
    lines: tuple  # (LineVerdict, ...)
