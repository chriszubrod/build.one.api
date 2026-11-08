"""U-778d1 -- invoice payload builder (design u778d section 5). Pure: no I/O.

`build_invoice` is a pure function of (proposal, verified charges, resolved fee items) -> the QBO
create payload AND the canonical expected invoice. Money enters the payload as JSON numbers built from
`Decimal` at exactly one point, `json_number` (integral -> int, else float of the Decimal's shortest
repr); no arithmetic is ever done on those floats.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence, Union

from .canonical import CanonicalInvoice, canonicalize
from .model import NON_TAX, RC_TYPE, FeeItem, LiveCharge, LiveChargeLine, Proposal, cents

DRY_RUN_NOTE = "build.one push dry-run"  # the write path substitutes the attempt marker


def json_number(d: Decimal) -> Union[int, float]:
    """The single Decimal -> JSON-number conversion point. Integral -> int (exact); otherwise the float is
    asserted to round-trip (`Decimal(str(float(d))) == d`) or this raises: a binary float must never alter a value
    that was verified. (Live QBO JSON is decoded as float; it is exact within the |x| < 1e11 money bound.)"""
    if d == d.to_integral_value():
        return int(d)
    f = float(d)
    if Decimal(str(f)) != d:
        raise ValueError(f"{d} does not survive a binary-float round trip")
    return f


def _ref(v: str) -> dict:
    return {"value": v}


def _charge_line(charge: LiveCharge, ln: LiveChargeLine) -> dict:
    amount = ln.amount
    detail: dict = {}
    if charge.txn_date:
        detail["ServiceDate"] = charge.txn_date
    if ln.item_ref:
        detail["ItemRef"] = _ref(ln.item_ref)
    if amount < 0:  # credits: Qty -1, UnitPrice positive, Amount negative (F4)
        detail["Qty"] = -1
        detail["UnitPrice"] = json_number(-amount)
    else:
        if ln.qty is not None:
            detail["Qty"] = json_number(ln.qty)
        if ln.unit_price is not None:
            detail["UnitPrice"] = json_number(ln.unit_price)
    if ln.item_account_ref:
        detail["ItemAccountRef"] = _ref(ln.item_account_ref)
    if ln.tax_code_ref:
        detail["TaxCodeRef"] = _ref(ln.tax_code_ref)
    if ln.markup_percent:  # markup RC line (cost or markup line): percent based
        mk = {"PercentBased": True, "Percent": json_number(ln.markup_percent), "Value": json_number(amount)}
    else:  # no-markup line (also credits)
        mk = {"PercentBased": False, "Value": json_number(amount)}
    if ln.item_account_ref:
        mk["MarkUpIncomeAccountRef"] = _ref(ln.item_account_ref)
    detail["MarkupInfo"] = mk
    line = {"DetailType": "SalesItemLineDetail", "Amount": json_number(amount)}
    if ln.description is not None:
        line["Description"] = ln.description
    line["LinkedTxn"] = [{"TxnId": charge.id, "TxnType": RC_TYPE, "TxnLineId": ln.line_id}]
    line["SalesItemLineDetail"] = detail
    return line


def _fee_line(fee, item: FeeItem) -> dict:
    amount = cents(fee.qty * fee.unit_price)
    detail: dict = {"ItemRef": _ref(item.item_ref), "Qty": json_number(fee.qty),
                    "UnitPrice": json_number(fee.unit_price)}
    if item.account_ref:
        detail["ItemAccountRef"] = _ref(item.account_ref)
    detail["TaxCodeRef"] = _ref(NON_TAX)
    return {"DetailType": "SalesItemLineDetail", "Amount": json_number(amount),
            "Description": fee.description, "SalesItemLineDetail": detail}


@dataclass(frozen=True)
class BuiltInvoice:
    payload: dict
    expected: CanonicalInvoice


def build_invoice(proposal: Proposal, *, customer_id: str, charges: Sequence[LiveCharge],
                  fee_items: Sequence[FeeItem], private_note: Optional[str] = DRY_RUN_NOTE,
                  sales_term_id: Optional[str] = None, home_currency: str = "USD") -> BuiltInvoice:
    """`charges[i]` is the verified RC for `proposal.source_lines[i]`; `fee_items[i]` the resolved item
    for `proposal.fee_lines[i]`. Inputs are not mutated."""
    if len(charges) != len(proposal.source_lines):
        raise ValueError(f"{len(charges)} charges for {len(proposal.source_lines)} source lines")
    if len(fee_items) != len(proposal.fee_lines):
        raise ValueError(f"{len(fee_items)} fee items for {len(proposal.fee_lines)} fee lines")
    if not proposal.due_date:
        raise ValueError("due_date is required")
    payload: dict = {"CustomerRef": _ref(customer_id), "TxnDate": proposal.txn_date, "DueDate": proposal.due_date}
    payload["DocNumber"] = proposal.doc_number
    if sales_term_id:  # the RESOLVED QBO term id (dbo PaymentTerm.QboId), never the local id
        payload["SalesTermRef"] = _ref(str(sales_term_id))
    if private_note is not None:
        payload["PrivateNote"] = private_note
    lines, rc_ids = [], []
    for charge in charges:
        for ln in charge.lines:
            lines.append(_charge_line(charge, ln))
        if charge.id not in rc_ids:
            rc_ids.append(charge.id)
    for fee, item in zip(proposal.fee_lines, fee_items):
        lines.append(_fee_line(fee, item))
    payload["Line"] = lines
    if rc_ids:
        payload["LinkedTxn"] = [{"TxnId": i, "TxnType": RC_TYPE} for i in rc_ids]
    return BuiltInvoice(payload, canonicalize(payload, home_currency=home_currency))
