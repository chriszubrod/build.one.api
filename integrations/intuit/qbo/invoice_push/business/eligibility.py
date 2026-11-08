"""U-778d1 -- eligibility rules (design u778d sections 3 + 4). Pure functions, no I/O.

Each rule is its own function returning a list of reason strings `"<code>: <detail>"`; the detail
carries BOTH values on a mismatch. `reason_code()` extracts the stable code. Economics (section 4.5):
Decimal only, cents quantised ROUND_HALF_UP.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal
from typing import Mapping, Optional

from .model import (
    NON_TAX, SUPPORTED_SOURCE_TYPES, LineVerdict, LiveCharge, LiveSourceLine, MalformedCharge, Proposal,
    ProposalContext, ProposalVerdict, SourceLineFacts, cents,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
MAX_MONEY = Decimal("1E11")  # |amount|, unit price, qty, total must stay below this (binary-float exactness bound)
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def reason_code(reason: str) -> str:
    return reason.split(":", 1)[0]


def reason(code: str, detail: str) -> str:
    """THE reason constructor (eligibility rules and the service's header reasons): `"<code>: <detail>"`."""
    return f"{code}: {detail}"


def unsupported_source_type_reason(source_type) -> str:
    return reason("unsupported_source_type", f"{source_type} is not one of {SUPPORTED_SOURCE_TYPES}")


def out_of_range(x) -> bool:
    return x is not None and abs(x) >= MAX_MONEY


def _valid_iso_date(v) -> bool:
    if not isinstance(v, str) or not _ISO_DATE.fullmatch(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


# ---- header (section 3) -----------------------------------------------------------
def check_header(proposal: Proposal, ctx: ProposalContext) -> list:
    out = []
    if not (proposal.doc_number or "").strip():
        out.append(reason("doc_number_empty", "doc_number is blank"))
    if ctx.doc_number_used_locally and not ctx.only_names_compared(ctx.doc_number_local_qbo_ids):
        out.append(reason("doc_number_used_locally", f"{proposal.doc_number!r} exists on a dbo.Invoice in the project"))
    if ctx.doc_number_used_in_qbo and not ctx.only_names_compared(ctx.doc_number_qbo_ids):
        out.append(reason("doc_number_used_in_qbo", f"{proposal.doc_number!r} exists on a QBO invoice in the realm"))
    if str(proposal.project_id) != str(ctx.project_id):
        out.append(reason("wrong_project", f"proposal={proposal.project_id} context={ctx.project_id}"))
    if not ctx.customer_id:
        out.append(reason("no_customer", "project maps to no QBO customer"))
    if not proposal.due_date:
        out.append(reason("due_date_missing", "due_date is required"))
    for name, v in (("txn_date", proposal.txn_date), ("due_date", proposal.due_date)):
        if v and not _valid_iso_date(v):
            out.append(reason("date_invalid", f"{name}={v!r} is not an ISO calendar date (YYYY-MM-DD)"))
        elif name == "txn_date" and not v:
            out.append(reason("date_invalid", "txn_date is required"))
    if not proposal.source_lines and not proposal.fee_lines:
        out.append(reason("no_lines", "proposal has no source lines and no fee lines"))
    seen = set()
    for key in proposal.source_lines:
        key = tuple(key)
        if key in seen:
            out.append(reason("duplicate_source_line", f"{key[0]}:{key[1]} listed twice"))
        seen.add(key)
    for i, fee in enumerate(proposal.fee_lines):
        if not (fee.description or "").strip() or fee.qty is None or fee.qty <= 0 or fee.unit_price is None:
            out.append(reason("fee_line_invalid", f"fee line {i}: description={fee.description!r} qty={fee.qty} "
                                              f"unit_price={fee.unit_price}"))
        elif (out_of_range(fee.qty) or out_of_range(fee.unit_price)
              or out_of_range(fee.qty * fee.unit_price)):
            out.append(reason("amount_out_of_range", f"fee line {i}: qty={fee.qty} unit_price={fee.unit_price} "
                                                 f"(|x| must be < {MAX_MONEY:f})"))
    return out


# ---- per source line: local (section 3 / 4.1) --------------------------------------
def check_source_type(f: SourceLineFacts) -> list:
    if f.source_type not in SUPPORTED_SOURCE_TYPES:
        return [unsupported_source_type_reason(f.source_type)]
    return []


def check_local_state(f: SourceLineFacts, ctx: ProposalContext) -> list:
    out = []
    if str(f.project_id) != str(ctx.project_id):
        out.append(reason("wrong_project", f"line project={f.project_id} proposal project={ctx.project_id}"))
    if f.parent_is_draft:
        out.append(reason("parent_draft", "parent document is a draft"))
    if f.parent_deleted:
        out.append(reason("parent_deleted", "parent document is deleted"))
    if not f.is_billable:
        out.append(reason("not_billable", "IsBillable is false locally"))
    ili_ok = (len(f.ili_invoice_qbo_ids) == len(f.referenced_by_ili_ids)
              and ctx.only_names_compared(f.ili_invoice_qbo_ids))
    if f.is_billed and not ili_ok:
        out.append(reason("locally_billed", "IsBilled is true locally"))
    if f.referenced_by_ili_ids and not ili_ok:
        out.append(reason("already_on_invoice", f"referenced by InvoiceLineItem ids {list(f.referenced_by_ili_ids)}"))
    if f.sub_cost_code_is_holding:
        out.append(reason("holding_code", "sub cost code is a holding code"))
    return out


def check_identity(f: SourceLineFacts) -> list:
    if not f.parent_qbo_id or not f.line_qbo_id:
        return [reason("source_not_in_qbo", f"dbo has no QBO identity (parent={f.parent_qbo_id} "
                                        f"line={f.line_qbo_id})")]
    return []


def check_staged_link(links, ctx: ProposalContext) -> list:
    """`links`: StagedInvoiceLink rows for the LIVE charge (looked up by the live charge id, never the staged one)."""
    if links and not ctx.only_names_compared([lk.invoice_qbo_id for lk in links]):
        # count only: the staged invoices may belong to ANOTHER customer, so their ids are never echoed
        return [reason("charge_on_staged_invoice",
                       f"{len(links)} staged qbo.InvoiceLine(s) already link this line's charge")]
    return []


# ---- live (section 4.2 / 4.3) ------------------------------------------------------
def check_live_source(live: Optional[LiveSourceLine], billed_ok: bool = False) -> list:
    """`billed_ok` (compare mode only): HasBeenBilled is the consequence of the compared invoice."""
    if live is None:
        return [reason("source_not_in_qbo", "source line not found live in QBO")]
    out = []
    if live.billable_status == "HasBeenBilled" and billed_ok:
        pass
    elif live.billable_status != "Billable":
        out.append(reason("not_billable_in_qbo", f"BillableStatus={live.billable_status} (need Billable)"))
    if not live.charge_id:
        out.append(reason("no_reimburse_charge", "source line carries no ReimburseCharge LinkedTxn"))
    return out


def check_charge_range(charge: LiveCharge) -> list:
    bad = [ln.line_id for ln in charge.lines
           if out_of_range(ln.amount) or out_of_range(ln.unit_price) or out_of_range(ln.qty)]
    if bad:
        return [reason("amount_out_of_range", f"charge {charge.id} lines {bad} have |amount|/unit price/qty >= {MAX_MONEY:f}")]
    return []


def check_live_charge(charge: Optional[LiveCharge], ctx: ProposalContext) -> list:
    if charge is None:
        return [reason("charge_not_found", "ReimburseCharge not found live")]
    out = []
    if charge.has_been_invoiced and not ctx.only_names_compared(charge.invoice_ids):
        out.append(reason("charge_invoiced", f"ReimburseCharge {charge.id} HasBeenInvoiced=true"))
    if (charge.customer_id or None) != (ctx.customer_id or None):
        # never echo the OTHER customer's QBO id
        out.append(reason("customer_mismatch", "charge belongs to a different customer"))
    if charge.currency != ctx.home_currency:
        out.append(reason("foreign_currency", f"charge currency={charge.currency} home={ctx.home_currency}"))
    return out


def check_taxable(charge: LiveCharge) -> list:
    bad = [ln.line_id for ln in charge.lines if ln.tax_code_ref != NON_TAX]
    if bad:
        return [reason("taxable_line", f"charge {charge.id} lines {bad} have a tax code other than {NON_TAX}")]
    return []


# ---- economics (section 4.5) -------------------------------------------------------
def charge_figures(charge: LiveCharge) -> tuple:
    """(rc_cost, rc_total, rc_pct): cost = the item-bearing line (else the first), total = sum of lines,
    pct = the cost line's markup percent (absent = 0)."""
    cost_line = next((ln for ln in charge.lines if ln.item_ref), charge.lines[0] if charge.lines else None)
    if cost_line is None:
        return ZERO, ZERO, ZERO
    total = sum((ln.amount for ln in charge.lines), ZERO)
    return cents(cost_line.amount), cents(total), cost_line.markup_percent or ZERO


def check_economics(f: SourceLineFacts, charge: LiveCharge) -> list:
    rc_cost, rc_total, rc_pct = charge_figures(charge)
    out = []
    if f.source_type == "BillCredit":
        if rc_pct != 0 or any(ln.has_markup_info or ln.markup_percent for ln in charge.lines):
            out.append(reason("credit_shape", f"BillCredit charge {charge.id} carries RC MarkupInfo (must be absent; "
                                          f"percent={rc_pct})"))
        if f.billable_amount is None:
            out.append(reason("amount_mismatch", f"BillableAmount is missing; charge cost={rc_cost}"))
            return out
        want = -abs(cents(f.billable_amount))
        if rc_cost != want:
            out.append(reason("amount_mismatch", f"local=-abs(BillableAmount)={want} qbo cost={rc_cost}"))
        if rc_total != rc_cost:
            out.append(reason("credit_shape", f"qbo total={rc_total} != cost={rc_cost}"))
        return out
    if rc_cost < 0 or rc_total < 0:
        out.append(reason("credit_shape", f"{f.source_type} line but charge {charge.id} is a credit "
                                      f"(cost={rc_cost} total={rc_total})"))
    if f.amount is None:
        out.append(reason("amount_mismatch", f"Amount is missing; qbo cost={rc_cost}"))
        return out
    amount = cents(f.amount)
    if amount != rc_cost:
        out.append(reason("amount_mismatch", f"local={amount} qbo cost={rc_cost}"))
    markup = f.markup or ZERO
    if f.price is None:
        if markup != 0:
            out.append(reason("price_missing_with_markup", f"Price is NULL but Markup={markup}"))
            price = None
        else:
            price = amount
    else:
        price = cents(f.price)
    if price is not None and price != rc_total:
        out.append(reason("price_mismatch", f"local={price} qbo total={rc_total}"))
    pct = markup * 100  # exact: .3501 * 100 == 35.01
    if pct != rc_pct:
        out.append(reason("markup_mismatch", f"local={pct.normalize():f} qbo percent={rc_pct.normalize():f}"))
    for label, v in (("Amount", f.amount), ("Price", f.price), ("BillableAmount", f.billable_amount)):
        if out_of_range(v):
            out.append(reason("amount_out_of_range", f"local {label}={v} (|x| must be < {MAX_MONEY:f})"))
    return out


def check_staged_charge_match(f: SourceLineFacts, live: LiveSourceLine) -> list:
    """Warning only -- the live link wins (staging lags). Every staged charge id is compared (duplicates collapse)."""
    staged = sorted({str(c) for c in f.staged_charge_ids if c})
    if staged and live.charge_id and staged != [str(live.charge_id)]:
        msg = reason("staged_charge_mismatch", f"staged={','.join(staged)} live={live.charge_id}")
        logger.warning("%s (%s:%s)", msg, f.source_type, f.source_line_id)
        return [msg]
    return []


# ---- composition -------------------------------------------------------------------
def verify_source_line(f: SourceLineFacts, live: Optional[LiveSourceLine], charge, ctx: ProposalContext,
                       staged_links=()) -> LineVerdict:
    """`charge`: LiveCharge | MalformedCharge | None. `staged_links`: staged invoice links for the LIVE charge id."""
    reasons = check_source_type(f)
    if reasons:
        return LineVerdict(f.source_type, f.source_line_id, False, tuple(reasons), (), None)
    reasons += check_local_state(f, ctx)
    reasons += check_identity(f)
    reasons += check_staged_link(staged_links, ctx)
    billed_ok = isinstance(charge, LiveCharge) and ctx.only_names_compared(charge.invoice_ids)
    reasons += check_live_source(live, billed_ok=billed_ok)
    warnings = check_staged_charge_match(f, live) if live is not None else []
    charge_id = live.charge_id if live is not None else None
    if live is not None and live.charge_id:
        if charge is not None and str(charge.id) != str(live.charge_id):
            reasons.append(reason("charge_not_found", f"live link names charge {live.charge_id}, got {charge.id}"))
        elif isinstance(charge, MalformedCharge):
            reasons.append(reason("charge_malformed", f"charge {charge.id}: {charge.why}"))
        else:
            reasons += check_live_charge(charge, ctx)
            if charge is not None:
                reasons += check_charge_range(charge)
                reasons += check_taxable(charge)
                reasons += check_economics(f, charge)
    return LineVerdict(f.source_type, f.source_line_id, not reasons, tuple(reasons), tuple(warnings), charge_id)


def evaluate_proposal(proposal: Proposal, ctx: ProposalContext, facts: Mapping, live: Mapping,
                      charges: Mapping, staged_links: Optional[Mapping] = None) -> ProposalVerdict:
    """facts/live keyed by (source_type, source_line_id); charges keyed by RC id; staged_links keyed by the LIVE
    RC id (-> tuple of StagedInvoiceLink)."""
    staged_links = staged_links or {}
    header = check_header(proposal, ctx)
    verdicts, seen_charge = [], {}
    for key in proposal.source_lines:
        key = tuple(key)
        f = facts.get(key)
        if f is None:
            verdicts.append(LineVerdict(key[0], key[1], False,
                                        (reason("source_not_in_qbo", "no dbo facts for this source line"),)))
            continue
        lv = live.get(key)
        cid = lv.charge_id if lv and lv.charge_id else None
        v = verify_source_line(f, lv, charges.get(cid) if cid else None, ctx,
                               staged_links=tuple(staged_links.get(cid, ())) if cid else ())
        if v.charge_id:
            if v.charge_id in seen_charge:
                header.append(reason("duplicate_charge", f"{key[0]}:{key[1]} and {seen_charge[v.charge_id]} share "
                                                     f"charge {v.charge_id}"))
            else:
                seen_charge[v.charge_id] = f"{key[0]}:{key[1]}"
        verdicts.append(v)
    ok = not header and all(v.ok for v in verdicts)
    return ProposalVerdict(ok, tuple(header), tuple(verdicts))
