"""U-778d1 b1 -- eligibility (design u778d sections 3 + 4). Every rule fires on a fixture where the
right answer and the wrong answer differ. Pure: no DB / HTTP."""
import ast
import json
import logging
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from integrations.intuit.qbo.invoice_push.business import eligibility as el
from integrations.intuit.qbo.invoice_push.business.model import (
    FeeLine, LiveCharge, LiveSourceLine, Proposal, ProposalContext, SourceLineFacts,
)

FX = Path(__file__).parent / "fixtures" / "u778d1"
D = Decimal


def qbo_rc(rc_id="900", lines=None, customer="1383", currency="USD", invoiced=False, tax="NON"):
    """lines: [(amount, markup_pct_or_None, has_item)]"""
    out = []
    for i, (amt, pct, has_item) in enumerate(lines or [(100.0, None, True)], start=1):
        det = {"ItemAccountRef": {"value": "6" if has_item else "162"}, "TaxCodeRef": {"value": tax}}
        if has_item:
            det.update({"ItemRef": {"value": "537"}, "Qty": 1, "UnitPrice": amt})
        if pct is not None:
            det["MarkupInfo"] = {"Percent": pct}
        out.append({"Id": str(i), "Description": f"d{i}", "Amount": amt, "DetailType": "ReimburseLineDetail",
                    "ReimburseLineDetail": det})
    return LiveCharge.from_qbo({"Id": rc_id, "SyncToken": "3", "HasBeenInvoiced": invoiced,
                                "CustomerRef": {"value": customer}, "CurrencyRef": {"value": currency},
                                "TxnDate": "2026-03-25", "Line": out})


def facts(**kw):
    base = dict(source_type="Bill", source_line_id=11, project_id=7, parent_is_draft=False,
                parent_deleted=False, is_billable=True, is_billed=False, referenced_by_ili_ids=(),
                sub_cost_code_is_holding=False, amount=D("100.00"), markup=None, price=None,
                billable_amount=None, parent_qbo_id="P1", line_qbo_id="L1", staged_charge_ids=("900",),
                ili_invoice_qbo_ids=())
    base.update(kw)
    return SourceLineFacts(**base)


CTX = ProposalContext(project_id=7, customer_id="1383", home_currency="USD",
                      doc_number_used_locally=False, doc_number_used_in_qbo=False)
LIVE = LiveSourceLine(billable_status="Billable", charge_id="900")


def codes(reasons):
    return sorted({el.reason_code(r) for r in reasons})


def verify(f=None, live=LIVE, charge=None, ctx=CTX):
    return el.verify_source_line(f or facts(), live, charge or qbo_rc(), ctx)


# ---- happy path -----------------------------------------------------------------
def test_clean_bill_passes():
    v = verify()
    assert v.ok and v.reasons == () and v.charge_id == "900"


def test_markup_bill_passes_with_real_fixture():
    charge = LiveCharge.from_qbo(json.loads((FX / "preflight_rc_68056.json").read_text()))
    f = facts(amount=D("32.50"), markup=D("0.5"), price=D("48.75"), staged_charge_ids=("68056",))
    assert verify(f, LiveSourceLine("Billable", "68056"), charge).ok


# ---- per-rule ---------------------------------------------------------------------
@pytest.mark.parametrize("st", ["EmployeeLabor", "ExpenseRefund", "Mystery"])
def test_unsupported_source_type(st):
    assert codes(el.check_source_type(facts(source_type=st))) == ["unsupported_source_type"]
    assert el.check_source_type(facts(source_type="Expense")) == []


def test_wrong_project():
    assert codes(el.check_local_state(facts(project_id=8), CTX)) == ["wrong_project"]


@pytest.mark.parametrize("kw,code", [
    (dict(parent_is_draft=True), "parent_draft"), (dict(parent_deleted=True), "parent_deleted"),
    (dict(is_billable=False), "not_billable"), (dict(is_billed=True), "locally_billed"),
    (dict(referenced_by_ili_ids=(5,)), "already_on_invoice"), (dict(sub_cost_code_is_holding=True), "holding_code")])
def test_local_state_rules(kw, code):
    assert codes(el.check_local_state(facts(**kw), CTX)) == [code]


def test_staged_invoice_line_blocks():
    assert codes(el.check_staged_link((link(),), CTX)) == ["charge_on_staged_invoice"]
    assert el.check_staged_link((), CTX) == []


@pytest.mark.parametrize("kw", [dict(parent_qbo_id=None), dict(line_qbo_id=None), dict(parent_qbo_id="")])
def test_identity_missing_is_source_not_in_qbo(kw):
    assert codes(el.check_identity(facts(**kw))) == ["source_not_in_qbo"]


def test_live_source_rules():
    assert codes(el.check_live_source(None)) == ["source_not_in_qbo"]
    assert codes(el.check_live_source(LiveSourceLine("NotBillable", "900"))) == ["not_billable_in_qbo"]
    assert codes(el.check_live_source(LiveSourceLine("HasBeenBilled", "900"))) == ["not_billable_in_qbo"]
    assert codes(el.check_live_source(LiveSourceLine("Billable", None))) == ["no_reimburse_charge"]
    assert el.check_live_source(LIVE) == []


def test_live_charge_rules():
    assert codes(el.check_live_charge(None, CTX)) == ["charge_not_found"]
    assert codes(el.check_live_charge(qbo_rc(invoiced=True), CTX)) == ["charge_invoiced"]
    assert codes(el.check_live_charge(qbo_rc(customer="2"), CTX)) == ["customer_mismatch"]
    assert codes(el.check_live_charge(qbo_rc(currency="CAD"), CTX)) == ["foreign_currency"]
    assert el.check_live_charge(qbo_rc(), CTX) == []


def test_customer_mismatch_does_not_echo_the_other_customer():
    """B3: another customer's QBO id never leaves the service."""
    (r,) = el.check_live_charge(qbo_rc(customer="7777123"), CTX)
    assert el.reason_code(r) == "customer_mismatch" and "7777123" not in r
    assert "different customer" in r


def test_staged_invoice_reason_does_not_echo_foreign_ids():
    """B4: count only -- no invoice / line / charge ids of (possibly another customer's) staged invoices."""
    links = (link(inv="8888456", line="9999789"), link(inv="8888457", line="9999790"))
    (r,) = el.check_staged_link(links, CTX)
    assert el.reason_code(r) == "charge_on_staged_invoice"
    for leaked in ("8888456", "9999789", "8888457", "9999790", str(links[0].charge_id)):
        assert leaked not in r
    assert "2" in r


def test_taxable_line():
    assert codes(el.check_taxable(qbo_rc(tax="TAX"))) == ["taxable_line"]
    assert el.check_taxable(qbo_rc()) == []


# ---- economics ---------------------------------------------------------------------
def test_amount_mismatch_reports_both_values():
    rs = el.check_economics(facts(amount=D("100.01")), qbo_rc())
    # price falls back to Amount when NULL, so it disagrees with the charge total too
    assert codes(rs) == ["amount_mismatch", "price_mismatch"]
    r = next(x for x in rs if el.reason_code(x) == "amount_mismatch")
    assert "100.01" in r and "100.00" in r


def test_cents_round_half_up():
    # 100.005 -> 100.01 (HALF_UP); banker's rounding would give 100.00
    assert el.check_economics(facts(amount=D("100.005")), qbo_rc(lines=[(100.01, None, True)])) == []
    assert codes(el.check_economics(facts(amount=D("100.005")), qbo_rc())) == ["amount_mismatch", "price_mismatch"]


def test_price_mismatch():
    ch = qbo_rc(lines=[(100.0, 50, True), (50.0, 50, False)])
    assert el.check_economics(facts(markup=D("0.5"), price=D("150.00")), ch) == []
    assert codes(el.check_economics(facts(markup=D("0.5"), price=D("149.99")), ch)) == ["price_mismatch"]


def test_price_none_with_no_or_zero_markup_uses_amount():
    assert el.check_economics(facts(price=None, markup=None), qbo_rc()) == []
    assert el.check_economics(facts(price=None, markup=D("0")), qbo_rc()) == []


def test_price_none_with_markup_refuses():
    ch = qbo_rc(lines=[(100.0, 50, True), (50.0, 50, False)])
    assert codes(el.check_economics(facts(price=None, markup=D("0.5")), ch)) == ["price_missing_with_markup"]


def test_markup_35_01_vs_35_is_exact():
    ch = qbo_rc(lines=[(100.0, 35.01, True), (35.01, 35.01, False)])
    assert el.check_economics(facts(markup=D("0.3501"), price=D("135.01")), ch) == []
    (r,) = el.check_economics(facts(markup=D("0.35"), price=D("135.01")), ch)
    assert el.reason_code(r) == "markup_mismatch" and "35" in r and "35.01" in r
    # and the converse: the charge has no markup but the local line says 35.01
    assert codes(el.check_economics(facts(markup=D("0.3501"), price=D("135.01")), qbo_rc())) == \
        ["markup_mismatch", "price_mismatch"]


def test_absent_markup_equals_zero():
    assert el.check_economics(facts(markup=None), qbo_rc()) == []
    assert el.check_economics(facts(markup=D("0")), qbo_rc()) == []
    assert codes(el.check_economics(facts(markup=D("0.5")), qbo_rc())) == ["markup_mismatch", "price_missing_with_markup"]


def credit_rc(amount=-15.34, extra_markup=False):
    lines = [(amount, 50 if extra_markup else None, True)]
    return qbo_rc(lines=lines)


def bc(**kw):
    base = dict(source_type="BillCredit", amount=None, markup=None, price=None, billable_amount=D("15.34"))
    base.update(kw)
    return facts(**base)


def test_billcredit_ok_regardless_of_billable_amount_sign():
    assert el.check_economics(bc(), credit_rc()) == []
    assert el.check_economics(bc(billable_amount=D("-15.34")), credit_rc()) == []


def test_billcredit_wrong_sign_or_amount():
    assert "amount_mismatch" in codes(el.check_economics(bc(), credit_rc(15.34)))
    assert codes(el.check_economics(bc(billable_amount=D("15.35")), credit_rc())) == ["amount_mismatch"]
    assert codes(el.check_economics(bc(billable_amount=None), credit_rc())) == ["amount_mismatch"]


def test_billcredit_with_rc_markup_is_credit_shape():
    assert "credit_shape" in codes(el.check_economics(bc(), credit_rc(extra_markup=True)))


def test_billcredit_total_not_cost_is_credit_shape():
    ch = qbo_rc(lines=[(-15.34, None, True), (-1.0, None, False)])
    assert "credit_shape" in codes(el.check_economics(bc(), ch))


def test_credit_rc_on_a_bill_is_credit_shape():
    assert "credit_shape" in codes(el.check_economics(facts(amount=D("-15.34")), credit_rc()))


def test_staged_charge_mismatch_is_warning_only(caplog):
    with caplog.at_level(logging.WARNING):
        v = verify(facts(staged_charge_ids=("OLD",)))
    assert v.ok and v.reasons == ()
    assert [el.reason_code(w) for w in v.warnings] == ["staged_charge_mismatch"]
    assert any("staged_charge_mismatch" in r.message for r in caplog.records)
    assert verify(facts(staged_charge_ids=())).warnings == ()


def test_verify_collects_every_reason_and_skips_live_checks_without_live_data():
    v = verify(facts(is_billed=True, sub_cost_code_is_holding=True), live=None, charge=None)
    assert not v.ok
    assert {"locally_billed", "holding_code", "source_not_in_qbo"} <= set(codes(v.reasons))
    v2 = el.verify_source_line(facts(), LIVE, None, CTX)
    assert codes(v2.reasons) == ["charge_not_found"]


def test_live_charge_id_differing_from_staged_uses_live():
    v = el.verify_source_line(facts(staged_charge_ids=("OLD",)), LiveSourceLine("Billable", "900"), qbo_rc("900"), CTX)
    assert v.ok and v.charge_id == "900"


def test_charge_object_must_match_live_charge_id():
    v = el.verify_source_line(facts(), LiveSourceLine("Billable", "901"), qbo_rc("900"), CTX)
    assert "charge_not_found" in codes(v.reasons)


# ---- header / proposal -------------------------------------------------------------
def prop(**kw):
    base = dict(project_id=7, doc_number="INV-1", txn_date="2026-11-06", due_date="2026-11-06", payment_term_id=None,
                source_lines=(("Bill", 11),), fee_lines=())
    base.update(kw)
    return Proposal(**base)


def test_header_rules():
    assert el.check_header(prop(), CTX) == []
    assert codes(el.check_header(prop(doc_number="  "), CTX)) == ["doc_number_empty"]
    assert codes(el.check_header(prop(), replace(CTX, doc_number_used_locally=True))) == ["doc_number_used_locally"]
    assert codes(el.check_header(prop(), replace(CTX, doc_number_used_in_qbo=True))) == ["doc_number_used_in_qbo"]
    assert codes(el.check_header(prop(source_lines=()), CTX)) == ["no_lines"]
    assert codes(el.check_header(prop(), replace(CTX, project_id=8))) == ["wrong_project"]
    assert codes(el.check_header(prop(), replace(CTX, customer_id=None))) == ["no_customer"]
    assert codes(el.check_header(prop(source_lines=(("Bill", 11), ("Bill", 11))), CTX)) == ["duplicate_source_line"]


def test_fee_line_validation():
    ok = FeeLine(1, "Fee", D("1"), D("10"))
    assert el.check_header(prop(source_lines=(), fee_lines=(ok,)), CTX) == []
    for bad in (FeeLine(1, "", D("1"), D("10")), FeeLine(1, "Fee", D("0"), D("10"))):
        assert codes(el.check_header(prop(source_lines=(), fee_lines=(bad,)), CTX)) == ["fee_line_invalid"]


def test_evaluate_clean_and_duplicate_charge():
    p = prop(source_lines=(("Bill", 11), ("Bill", 12)))
    f = {("Bill", 11): facts(), ("Bill", 12): facts(source_line_id=12, staged_charge_ids=("900",))}
    live = {k: LIVE for k in f}
    pv = el.evaluate_proposal(p, CTX, f, live, {"900": qbo_rc()})
    assert not pv.ok and "duplicate_charge" in codes(pv.header_reasons)

    p1 = prop()
    pv1 = el.evaluate_proposal(p1, CTX, {("Bill", 11): facts()}, {("Bill", 11): LIVE}, {"900": qbo_rc()})
    assert pv1.ok and [v.ok for v in pv1.lines] == [True]


def test_evaluate_missing_facts_is_a_refusal_not_a_crash():
    pv = el.evaluate_proposal(prop(), CTX, {}, {}, {})
    assert not pv.ok and codes(pv.lines[0].reasons) == ["source_not_in_qbo"]


def test_evaluate_single_bad_line_fails_proposal():
    p = prop(source_lines=(("Bill", 11), ("Bill", 12)))
    c2 = qbo_rc("901", lines=[(50.0, None, True)])
    f = {("Bill", 11): facts(), ("Bill", 12): facts(source_line_id=12, amount=D("50.00"), staged_charge_ids=("901",),
                                                     is_billed=True)}
    live = {("Bill", 11): LIVE, ("Bill", 12): LiveSourceLine("Billable", "901")}
    pv = el.evaluate_proposal(p, CTX, f, live, {"900": qbo_rc(), "901": c2})
    assert not pv.ok and [v.ok for v in pv.lines] == [True, False]


# ---- purity --------------------------------------------------------------------------
def test_package_imports_no_io():
    root = Path(el.__file__).resolve().parent
    # b2 added the I/O layers (api/, persistence/, external/) and business/{service,ports,mode}.py; the purity
    # guard covers the four pure bite-1 modules.
    pure = [root / f"{n}.py" for n in ("model", "eligibility", "payload", "canonical")]
    forbidden = ("shared", "httpx", "requests", "pyodbc", "aiohttp", "os", "socket", "urllib",
                 "integrations.intuit.qbo.client", "integrations.intuit.qbo.auth")
    seen = []
    for path in pure:
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                if n == "integrations.intuit.qbo.invoice_push" or n.startswith("integrations.intuit.qbo.invoice_push."):
                    continue
                if any(n == f or n.startswith(f + ".") for f in forbidden):
                    seen.append((path.name, n))
    assert seen == []


# ======================= fix round 1 (U-778d1 f1) =======================
def raw_rc(rc_id="900", **over):
    rc = {"Id": rc_id, "SyncToken": "3", "HasBeenInvoiced": False, "CustomerRef": {"value": "1383"},
          "CurrencyRef": {"value": "USD"}, "TxnDate": "2026-03-25",
          "Line": [{"Id": "1", "Description": "d", "Amount": 100.0, "DetailType": "ReimburseLineDetail",
                    "ReimburseLineDetail": {"ItemRef": {"value": "537"}, "Qty": 1, "UnitPrice": 100.0,
                                            "ItemAccountRef": {"value": "6"}, "TaxCodeRef": {"value": "NON"}}}]}
    rc.update(over)
    return rc


def link(charge="900", inv="555", line="7"):
    from integrations.intuit.qbo.invoice_push.business.model import StagedInvoiceLink
    return StagedInvoiceLink(charge, inv, line)


def test_f1_staged_links_are_keyed_by_the_live_charge():
    f = facts(staged_charge_ids=("OLD",))
    # a staged invoice line links the LIVE charge although staging still names OLD -> blocks
    v = el.verify_source_line(f, LIVE, qbo_rc("900"), CTX, staged_links=(link("900"),))
    assert "charge_on_staged_invoice" in codes(v.reasons)
    # staged OLD charge has invoice links, but the live replacement has none -> eligible (live wins)
    v2 = el.verify_source_line(f, LIVE, qbo_rc("900"), CTX, staged_links=())
    assert v2.ok
    # and through evaluate_proposal, links are looked up by the live charge id only
    pv = el.evaluate_proposal(prop(), CTX, {("Bill", 11): f}, {("Bill", 11): LIVE}, {"900": qbo_rc("900")},
                              staged_links={"OLD": (link("OLD"),)})
    assert pv.ok
    pv = el.evaluate_proposal(prop(), CTX, {("Bill", 11): f}, {("Bill", 11): LIVE}, {"900": qbo_rc("900")},
                              staged_links={"900": (link("900"),)})
    assert not pv.ok


def test_f1_duplicate_staged_charge_ids_warn_when_they_disagree():
    ok = el.verify_source_line(facts(staged_charge_ids=("900", "900")), LIVE, qbo_rc("900"), CTX)
    assert ok.warnings == ()
    bad = el.verify_source_line(facts(staged_charge_ids=("900", "901")), LIVE, qbo_rc("900"), CTX)
    assert bad.ok and [el.reason_code(w) for w in bad.warnings] == ["staged_charge_mismatch"]
    assert "901" in bad.warnings[0]


def _mal(mut):
    rc = raw_rc()
    mut(rc)
    return rc


F2_CASES = {
    "id_missing": lambda r: r.pop("Id"),
    "id_nondigit": lambda r: r.update(Id="abc"),
    "invoiced_missing": lambda r: r.pop("HasBeenInvoiced"),
    "invoiced_null": lambda r: r.update(HasBeenInvoiced=None),
    "invoiced_string": lambda r: r.update(HasBeenInvoiced="false"),
    "customer_missing": lambda r: r.pop("CustomerRef"),
    "customer_empty": lambda r: r.update(CustomerRef={}),
    "no_lines": lambda r: r.update(Line=[]),
    "lines_missing": lambda r: r.pop("Line"),
    "line_id_missing": lambda r: r["Line"][0].pop("Id"),
    "line_id_empty": lambda r: r["Line"][0].update(Id=""),
    "line_id_nondigit": lambda r: r["Line"][0].update(Id="x1"),
    "amount_missing": lambda r: r["Line"][0].pop("Amount"),
    "amount_null": lambda r: r["Line"][0].update(Amount=None),
    "amount_string": lambda r: r["Line"][0].update(Amount="abc"),
}


@pytest.mark.parametrize("case", sorted(F2_CASES))
def test_f2_malformed_charge_is_refused(case):
    from integrations.intuit.qbo.invoice_push.business.model import ChargeMalformed
    with pytest.raises(ChargeMalformed):
        LiveCharge.from_qbo(_mal(F2_CASES[case]))


def test_f2_a_malformed_charge_gives_charge_malformed_never_eligible():
    from integrations.intuit.qbo.invoice_push.business.model import MalformedCharge
    v = el.verify_source_line(facts(), LIVE, MalformedCharge("900", "HasBeenInvoiced missing"), CTX)
    assert not v.ok and codes(v.reasons) == ["charge_malformed"]


def test_f2_complete_charge_still_parses_with_invoice_links():
    c = LiveCharge.from_qbo(raw_rc(LinkedTxn=[{"TxnId": "55", "TxnType": "Invoice"}]))
    assert c.invoice_ids == ("55",) and c.has_been_invoiced is False


def cmp_ctx(**kw):
    return replace(CTX, compare_invoice_id="9001", **kw)


def test_f3_charge_invoiced_tolerated_only_by_the_compared_invoice():
    mine = LiveCharge.from_qbo(raw_rc(HasBeenInvoiced=True, LinkedTxn=[{"TxnId": "9001", "TxnType": "Invoice"}]))
    other = LiveCharge.from_qbo(raw_rc(HasBeenInvoiced=True, LinkedTxn=[{"TxnId": "9999", "TxnType": "Invoice"}]))
    both = LiveCharge.from_qbo(raw_rc(HasBeenInvoiced=True, LinkedTxn=[{"TxnId": "9001", "TxnType": "Invoice"},
                                                                       {"TxnId": "9999", "TxnType": "Invoice"}]))
    none = LiveCharge.from_qbo(raw_rc(HasBeenInvoiced=True))
    assert verify_with(mine, cmp_ctx()).ok
    for c in (other, both, none):
        assert "charge_invoiced" in codes(verify_with(c, cmp_ctx()).reasons)
    assert "charge_invoiced" in codes(verify_with(mine, CTX).reasons)  # not compare mode


def verify_with(charge, ctx, f=None, live=LIVE, links=()):
    return el.verify_source_line(f or facts(), live, charge, ctx, staged_links=links)


def test_f3_staged_links_and_local_ili_tolerated_only_for_the_compared_invoice():
    c = qbo_rc("900")
    assert verify_with(c, cmp_ctx(), links=(link(inv="9001"),)).ok
    assert "charge_on_staged_invoice" in codes(verify_with(c, cmp_ctx(), links=(link(inv="9001"), link(inv="9999"))).reasons)
    assert "charge_on_staged_invoice" in codes(verify_with(c, CTX, links=(link(inv="9001"),)).reasons)
    mine = facts(referenced_by_ili_ids=(5,), ili_invoice_qbo_ids=("9001",), is_billed=True)
    assert verify_with(c, cmp_ctx(), f=mine).ok
    for ids in (("9999",), (None,), ("9001", "9999"), ()):
        f = facts(referenced_by_ili_ids=(5, 6)[:max(len(ids), 1)], ili_invoice_qbo_ids=ids)
        assert "already_on_invoice" in codes(verify_with(c, cmp_ctx(), f=f).reasons), ids


def test_f3_doc_number_tolerated_only_when_it_belongs_to_the_compared_invoice():
    assert el.check_header(prop(due_date="2026-11-06"),
                           cmp_ctx(doc_number_used_locally=True, doc_number_local_qbo_ids=("9001",),
                                   doc_number_used_in_qbo=True, doc_number_qbo_ids=("9001",))) == []
    r = el.check_header(prop(due_date="2026-11-06"),
                        cmp_ctx(doc_number_used_locally=True, doc_number_local_qbo_ids=("9001", "8"),
                                doc_number_used_in_qbo=True, doc_number_qbo_ids=("9999",)))
    assert codes(r) == ["doc_number_used_in_qbo", "doc_number_used_locally"]
    r = el.check_header(prop(due_date="2026-11-06"),
                        replace(CTX, doc_number_used_locally=True, doc_number_local_qbo_ids=("9001",)))
    assert codes(r) == ["doc_number_used_locally"]


def test_f3_has_been_billed_tolerated_only_with_the_compared_charge_link():
    mine = LiveCharge.from_qbo(raw_rc(HasBeenInvoiced=True, LinkedTxn=[{"TxnId": "9001", "TxnType": "Invoice"}]))
    billed = LiveSourceLine("HasBeenBilled", "900")
    assert verify_with(mine, cmp_ctx(), live=billed).ok
    assert "not_billable_in_qbo" in codes(verify_with(qbo_rc("900"), cmp_ctx(), live=billed).reasons)


def test_f10_amounts_at_or_above_1e11_are_refused():
    big = FeeLine(1, "Fee", D("1"), D("100000000000"))
    assert codes(el.check_header(prop(source_lines=(), fee_lines=(big,), due_date="2026-11-06"), CTX)) == ["amount_out_of_range"]
    big_qty = FeeLine(1, "Fee", D("100000000000"), D("0.01"))
    assert "amount_out_of_range" in codes(el.check_header(prop(source_lines=(), fee_lines=(big_qty,), due_date="2026-11-06"), CTX))
    ok = FeeLine(1, "Fee", D("1"), D("99999999999.99"))
    assert el.check_header(prop(source_lines=(), fee_lines=(ok,), due_date="2026-11-06"), CTX) == []
    huge_charge = qbo_rc("900", lines=[(1e11, None, True)])
    v = verify_with(huge_charge, CTX, f=facts(amount=D("100000000000")))
    assert "amount_out_of_range" in codes(v.reasons)
    assert "amount_out_of_range" in codes(verify_with(qbo_rc("900"), CTX, f=facts(price=D("100000000000"))).reasons)


def test_f11_credit_requires_markup_info_absent_not_just_falsy():
    cf = facts(source_type="BillCredit", billable_amount=D("-50.00"), amount=None)
    def credit(markup):
        rc = raw_rc(); ln = rc["Line"][0]; ln["Amount"] = -50.0
        ln["ReimburseLineDetail"]["UnitPrice"] = 50.0; ln["ReimburseLineDetail"]["Qty"] = -1
        if markup is not None:
            ln["ReimburseLineDetail"]["MarkupInfo"] = markup
        return LiveCharge.from_qbo(rc)
    assert verify_with(credit(None), CTX, f=cf).ok
    for mk in ({"Percent": 0}, {}, {"PercentBased": False}):
        assert "credit_shape" in codes(verify_with(credit(mk), CTX, f=cf).reasons), mk


def test_f8_f13_header_dates():
    assert codes(el.check_header(prop(due_date=None), CTX)) == ["due_date_missing"]
    assert codes(el.check_header(prop(due_date="2026-99-99"), CTX)) == ["date_invalid"]
    assert codes(el.check_header(prop(txn_date="not-a-date", due_date="2026-11-06"), CTX)) == ["date_invalid"]
    assert codes(el.check_header(prop(txn_date="20261106", due_date="2026-11-06"), CTX)) == ["date_invalid"]
    assert el.check_header(prop(due_date="2026-11-06"), CTX) == []
