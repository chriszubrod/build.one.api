"""U-778d1 -- canonical invoice form + comparison (design u778d section 5 canonical contract, 6.4). Pure.

Canonical form: references by `value` only (names are response noise); ISO date strings; `Decimal`
money at 2 dp, qty / unit price / percent at 4 dp; `SubTotalLineDetail` lines dropped; each linked line
must carry exactly one LinkedTxn of type ReimburseCharge -- violations are recorded in `violations` and
reported by `compare`, never silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Optional

from .model import CENT as MONEY, RC_TYPE, dec, ref_value

QTY = Decimal("0.0001")


def _q(x: Any, places: Decimal) -> Optional[Decimal]:
    d = dec(x)
    return None if d is None else d.quantize(places, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CanonicalMarkup:
    percent_based: Optional[bool]
    percent: Optional[Decimal]
    value: Optional[Decimal]
    account_ref: Optional[str]


@dataclass(frozen=True)
class CanonicalLine:
    description: Optional[str]
    amount: Optional[Decimal]
    service_date: Optional[str]
    item_ref: Optional[str]
    qty: Optional[Decimal]
    unit_price: Optional[Decimal]
    item_account_ref: Optional[str]
    tax_code_ref: Optional[str]
    markup: Optional[CanonicalMarkup]
    linked_txn: tuple  # ((TxnId, TxnType, TxnLineId), ...)
    line_id: Optional[str] = field(default=None, compare=False)  # QBO-assigned; checked separately


@dataclass(frozen=True)
class CanonicalInvoice:
    customer_ref: Optional[str]
    doc_number: Optional[str]
    txn_date: Optional[str]
    due_date: Optional[str]
    sales_term_ref: Optional[str]
    private_note: Optional[str]
    linked_txn: tuple  # ((TxnId, TxnType), ...) header level
    lines: tuple
    total_amt: Optional[Decimal]
    violations: tuple = ()
    currency_ref: Optional[str] = None  # CurrencyRef.value; absent on a payload/response == the home currency
    exchange_rate: Optional[Decimal] = None  # absent == 1


@dataclass(frozen=True)
class Diff:
    path: str
    expected: Any
    actual: Any


def _canon_line(ln: Mapping[str, Any], idx: int, violations: list) -> Optional[CanonicalLine]:
    dt = ln.get("DetailType")
    if dt == "SubTotalLineDetail":
        return None
    if dt != "SalesItemLineDetail":
        violations.append(f"Line[{idx}]: unsupported DetailType {dt!r}")
        return None
    d = ln.get("SalesItemLineDetail") or {}
    mk = d.get("MarkupInfo")
    markup = None
    if mk is not None:
        markup = CanonicalMarkup(
            percent_based=mk.get("PercentBased"),
            percent=_q(mk.get("Percent"), QTY),
            value=_q(mk.get("Value"), MONEY),
            account_ref=ref_value(mk.get("MarkUpIncomeAccountRef")),
        )
    linked = []
    for lt in ln.get("LinkedTxn") or []:
        linked.append((str(lt.get("TxnId")), lt.get("TxnType"),
                       None if lt.get("TxnLineId") is None else str(lt.get("TxnLineId"))))
    if linked and (len(linked) != 1 or linked[0][1] != RC_TYPE):
        violations.append(f"Line[{idx}]: linked line must have exactly one {RC_TYPE} LinkedTxn, got {linked}")
    return CanonicalLine(
        description=ln.get("Description"),
        amount=_q(ln.get("Amount"), MONEY),
        service_date=d.get("ServiceDate"),
        item_ref=ref_value(d.get("ItemRef")),
        qty=_q(d.get("Qty"), QTY),
        unit_price=_q(d.get("UnitPrice"), QTY),
        item_account_ref=ref_value(d.get("ItemAccountRef")),
        tax_code_ref=ref_value(d.get("TaxCodeRef")),
        markup=markup,
        linked_txn=tuple(linked),
        line_id=None if ln.get("Id") is None else str(ln.get("Id")),
    )


def canonicalize(invoice: Mapping[str, Any], *, home_currency: str = "USD",
                 require_total: bool = False) -> CanonicalInvoice:
    """A QBO invoice (or create-response `{"Invoice": ...}`, or our payload) -> canonical form.
    TotalAmt absent (a payload) = sum of the kept lines -- unless `require_total` (ACTUAL data from QBO), where an
    absent TotalAmt is a recorded violation and `total_amt` stays None. CurrencyRef absent == `home_currency`;
    ExchangeRate absent == 1."""
    if "Invoice" in invoice and isinstance(invoice["Invoice"], Mapping):
        invoice = invoice["Invoice"]
    violations: list = []
    lines = []
    for i, ln in enumerate(invoice.get("Line") or []):
        c = _canon_line(ln, i, violations)
        if c is not None:
            lines.append(c)
    total = _q(invoice.get("TotalAmt"), MONEY)
    if total is None and require_total:
        violations.append("TotalAmt is missing from the invoice")
    elif total is None:
        total = sum((ln.amount or Decimal("0") for ln in lines), Decimal("0")).quantize(MONEY)
    header_linked = tuple((str(lt.get("TxnId")), lt.get("TxnType")) for lt in invoice.get("LinkedTxn") or [])
    return CanonicalInvoice(
        customer_ref=ref_value(invoice.get("CustomerRef")),
        doc_number=invoice.get("DocNumber"),
        txn_date=invoice.get("TxnDate"),
        due_date=invoice.get("DueDate"),
        sales_term_ref=ref_value(invoice.get("SalesTermRef")),
        private_note=invoice.get("PrivateNote"),
        linked_txn=header_linked,
        lines=tuple(lines),
        total_amt=total,
        violations=tuple(violations),
        currency_ref=ref_value(invoice.get("CurrencyRef")) or home_currency,
        exchange_rate=_rate(invoice.get("ExchangeRate")),
    )


def _rate(x: Any) -> Decimal:
    d = dec(x)
    return Decimal("1") if d is None else d


_HEADER_FIELDS = ("customer_ref", "doc_number", "txn_date", "due_date", "sales_term_ref", "private_note",
                  "currency_ref", "exchange_rate")
_LINE_FIELDS = ("description", "amount", "service_date", "item_ref", "qty", "unit_price",
                "item_account_ref", "tax_code_ref")
_MARKUP_FIELDS = ("percent_based", "percent", "value", "account_ref")


def _unordered(lines) -> list:
    return sorted(repr(replace(ln, line_id=None)) for ln in lines)


def _line_diffs(i: int, e: CanonicalLine, a: CanonicalLine) -> list:
    out = []
    for f in _LINE_FIELDS:
        if getattr(e, f) != getattr(a, f):
            out.append(Diff(f"Line[{i}].{f}", getattr(e, f), getattr(a, f)))
    if e.markup != a.markup:
        if e.markup is None or a.markup is None:
            out.append(Diff(f"Line[{i}].markup", e.markup, a.markup))
        else:
            for f in _MARKUP_FIELDS:
                if getattr(e.markup, f) != getattr(a.markup, f):
                    out.append(Diff(f"Line[{i}].markup.{f}", getattr(e.markup, f), getattr(a.markup, f)))
    if e.linked_txn != a.linked_txn:
        out.append(Diff(f"Line[{i}].linked_txn", e.linked_txn, a.linked_txn))
    return out


def compare(expected: CanonicalInvoice, actual: CanonicalInvoice, *, require_line_ids: bool = False) -> list:
    """Diffs of `actual` against `expected`; empty list = identical (design 6.4).

    Header fields, then line count, then line-by-line in order, then TotalAmt. A different line count
    is ONE diff and stops the line-level comparison (a missing/extra line would otherwise cascade);
    a pure reordering is ONE diff; TotalAmt is compared only when no line differs (it is derived
    from the lines, so a line diff would otherwise double-report). Header LinkedTxn compares as a
    set (QBO's ordering is not contractual). `require_line_ids` additionally demands unique non-empty
    QBO line Ids on `actual` (a fresh GET), not applicable to a payload.
    """
    diffs = []
    for f in _HEADER_FIELDS:
        if getattr(expected, f) != getattr(actual, f):
            diffs.append(Diff(f, getattr(expected, f), getattr(actual, f)))
    if sorted(expected.linked_txn) != sorted(actual.linked_txn):
        diffs.append(Diff("linked_txn", expected.linked_txn, actual.linked_txn))
    for i, v in enumerate(actual.violations):
        diffs.append(Diff(f"violation[{i}]", None, v))
    if len(expected.lines) != len(actual.lines):
        diffs.append(Diff("line_count", len(expected.lines), len(actual.lines)))
        return diffs
    line_diffs = []
    for i, (e, a) in enumerate(zip(expected.lines, actual.lines)):
        line_diffs += _line_diffs(i, e, a)
    if line_diffs and _unordered(expected.lines) == _unordered(actual.lines):
        line_diffs = [Diff("Line.order", [l.description for l in expected.lines],
                           [l.description for l in actual.lines])]
    diffs += line_diffs
    if not line_diffs and expected.total_amt != actual.total_amt:
        diffs.append(Diff("total_amt", expected.total_amt, actual.total_amt))
    if require_line_ids:
        ids = [ln.line_id for ln in actual.lines]
        if any(not x for x in ids) or len(set(ids)) != len(ids):
            diffs.append(Diff("line_id", "unique non-empty", ids))
    return diffs
