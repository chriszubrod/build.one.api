"""U-778d1 b1 -- payload builder (design u778d section 5). Pure: no DB / HTTP."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from integrations.intuit.qbo.invoice_push.business.model import (
    FeeItem, FeeLine, LiveCharge, Proposal,
)
from integrations.intuit.qbo.invoice_push.business.payload import (
    DRY_RUN_NOTE, build_invoice, json_number,
)

FX = Path(__file__).parent / "fixtures" / "u778d1"


def load(name):
    return json.loads((FX / name).read_text())


def norm(o):
    return json.dumps(o, sort_keys=True)


def strip(o):
    """Drop response-only noise: reference names, line Id/LineNum, CustomExtensions."""
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items()
                if k not in ("name", "Id", "LineNum", "CustomExtensions")}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o


def proposal(sources, fees=(), **kw):
    base = dict(project_id=7, doc_number="LINKTEST-1", txn_date="2026-11-06", due_date="2026-11-06",
                payment_term_id=None, source_lines=tuple(sources), fee_lines=tuple(fees))
    base.update(kw)
    return Proposal(**base)


def rc(rc_id, amount, desc, item="172", acct="6", date="2026-03-25", qty=None, price=None):
    line = {"Id": "1", "Description": desc, "Amount": amount, "DetailType": "ReimburseLineDetail",
            "ReimburseLineDetail": {"ItemRef": {"value": item}, "ItemAccountRef": {"value": acct},
                                    "TaxCodeRef": {"value": "NON"}}}
    if qty is not None:
        line["ReimburseLineDetail"]["Qty"] = qty
        line["ReimburseLineDetail"]["UnitPrice"] = price
    return LiveCharge.from_qbo({"Id": rc_id, "SyncToken": "0", "HasBeenInvoiced": False,
                                "CustomerRef": {"value": "1383"}, "CurrencyRef": {"value": "USD"},
                                "TxnDate": date, "Line": [line]})


def test_reproduces_the_live_accepted_payload_exactly():
    charge = LiveCharge.from_qbo(load("preflight_rc_68056.json"))
    expected = load("linktest_invoice_payload.json")
    expected["DueDate"] = "2026-11-06"  # F8: always emitted (QBO defaulted it to the TxnDate in the live run)
    built = build_invoice(proposal([("Bill", 1)]), customer_id="1383", charges=[charge],
                          fee_items=[], private_note=expected["PrivateNote"])
    assert norm(built.payload) == norm(expected)


def test_default_private_note_is_the_dry_run_marker():
    charge = LiveCharge.from_qbo(load("preflight_rc_68056.json"))
    built = build_invoice(proposal([("Bill", 1)]), customer_id="1383", charges=[charge], fee_items=[])
    assert built.payload["PrivateNote"] == DRY_RUN_NOTE == "build.one push dry-run"


def test_no_markup_line_matches_qbo_built_shape():
    shapes = load("qbo_built_invoice_shapes.json")
    want = strip(shapes["Line"][3])  # Skimmer Cover 300, RC 77280
    charge = rc("77280", 300.0, "Skimmer Cover", item="469", date="2026-08-11", qty=1, price=300)
    built = build_invoice(proposal([("Expense", 5)]), customer_id="1356", charges=[charge], fee_items=[])
    assert built.payload["Line"] == [want]
    assert built.payload["Line"][0]["SalesItemLineDetail"]["MarkupInfo"] == {
        "PercentBased": False, "Value": 300, "MarkUpIncomeAccountRef": {"value": "6"}}


def test_credit_line_matches_qbo_built_shape_even_without_rc_qty():
    shapes = load("qbo_built_invoice_shapes.json")
    want = strip(shapes["Line"][0])  # Framing CREDIT -15.34, RC 76757
    charge = rc("76757", -15.34, "Framing CREDIT")  # synthesized: RC credit line has no Qty/UnitPrice
    built = build_invoice(proposal([("BillCredit", 5)]), customer_id="1356", charges=[charge], fee_items=[])
    line = built.payload["Line"][0]
    assert line == want
    d = line["SalesItemLineDetail"]
    assert d["Qty"] == -1 and d["UnitPrice"] == 15.34 and line["Amount"] == -15.34
    assert d["MarkupInfo"]["Value"] == -15.34 and d["MarkupInfo"]["PercentBased"] is False


def test_header_linked_txn_is_distinct_rcs_in_first_seen_order_with_terms_and_due():
    c1 = rc("77377", 10.0, "a", qty=1, price=10)
    c2 = rc("77379", 20.0, "b", qty=1, price=20)
    built = build_invoice(proposal([("Bill", 1), ("Bill", 2)], due_date="2026-12-06", payment_term_id="2"),
                          customer_id="1356", charges=[c2, c1], fee_items=[], sales_term_id="2")
    p = built.payload
    assert p["LinkedTxn"] == [{"TxnId": "77379", "TxnType": "ReimburseCharge"},
                              {"TxnId": "77377", "TxnType": "ReimburseCharge"}]
    assert p["DueDate"] == "2026-12-06" and p["SalesTermRef"] == {"value": "2"}


def test_sales_term_omitted_when_absent():
    c1 = rc("1", 10.0, "a", qty=1, price=10)
    p = build_invoice(proposal([("Bill", 1)]), customer_id="9", charges=[c1], fee_items=[]).payload
    assert "SalesTermRef" not in p


def test_fee_line_is_plain_unlinked():
    fee = FeeLine(sub_cost_code_id=3, description="Builder's fee", qty=Decimal("1"), unit_price=Decimal("1234.565"))
    item = FeeItem(item_ref="80", account_ref="191")
    c1 = rc("1", 10.0, "a", qty=1, price=10)
    p = build_invoice(proposal([("Bill", 1)], fees=[fee]), customer_id="9", charges=[c1], fee_items=[item]).payload
    last = p["Line"][-1]
    assert "LinkedTxn" not in last and "MarkupInfo" not in last["SalesItemLineDetail"]
    assert last["Amount"] == 1234.57  # ROUND_HALF_UP to cents
    assert last["SalesItemLineDetail"]["ItemRef"] == {"value": "80"}
    assert last["SalesItemLineDetail"]["ItemAccountRef"] == {"value": "191"}
    assert p["LinkedTxn"] == [{"TxnId": "1", "TxnType": "ReimburseCharge"}]


def test_fee_only_invoice_has_no_header_linked_txn():
    fee = FeeLine(3, "Fee", Decimal("2"), Decimal("10"))
    p = build_invoice(proposal([], fees=[fee]), customer_id="9", charges=[],
                      fee_items=[FeeItem("80", "191")]).payload
    assert "LinkedTxn" not in p and p["Line"][0]["Amount"] == 20


def test_fee_items_must_pair_with_fee_lines():
    fee = FeeLine(3, "Fee", Decimal("2"), Decimal("10"))
    with pytest.raises(ValueError):
        build_invoice(proposal([], fees=[fee]), customer_id="9", charges=[], fee_items=[])


def test_charges_must_pair_with_source_lines():
    with pytest.raises(ValueError):
        build_invoice(proposal([("Bill", 1)]), customer_id="9", charges=[], fee_items=[])


def test_expected_canonical_matches_canonical_of_payload_and_total():
    charge = LiveCharge.from_qbo(load("preflight_rc_68056.json"))
    built = build_invoice(proposal([("Bill", 1)]), customer_id="1383", charges=[charge], fee_items=[])
    assert built.expected.total_amt == Decimal("48.75")
    assert [l.amount for l in built.expected.lines] == [Decimal("32.50"), Decimal("16.25")]


def test_builder_does_not_mutate_inputs():
    raw = load("preflight_rc_68056.json")
    before = copy.deepcopy(raw)
    build_invoice(proposal([("Bill", 1)]), customer_id="1383", charges=[LiveCharge.from_qbo(raw)], fee_items=[])
    assert raw == before


@pytest.mark.parametrize("d,out", [(Decimal("50"), 50), (Decimal("50.00"), 50), (Decimal("32.50"), 32.5),
                                   (Decimal("-15.34"), -15.34), (Decimal("0.1"), 0.1)])
def test_json_number_conversion(d, out):
    r = json_number(d)
    assert r == out and type(r) is type(out)


# ======================= fix round 1 (U-778d1 f1) =======================
def test_f10_json_number_refuses_a_value_binary_floats_would_alter():
    with pytest.raises(ValueError):
        json_number(Decimal("99999999999999.99"))
    assert json_number(Decimal("99999999999.99")) == 99999999999.99
    assert Decimal(str(json_number(Decimal("99999999999.99")))) == Decimal("99999999999.99")
    assert json_number(Decimal("123456789012345678")) == 123456789012345678  # integral stays exact


def test_f8_due_date_always_emitted():
    c1 = rc("1", 10.0, "a", qty=1, price=10)
    p = build_invoice(proposal([("Bill", 1)], due_date="2026-11-06"), customer_id="9", charges=[c1],
                      fee_items=[]).payload
    assert p["DueDate"] == "2026-11-06"


def test_f7_sales_term_comes_from_the_resolved_qbo_id_not_the_local_id():
    c1 = rc("1", 10.0, "a", qty=1, price=10)
    p = build_invoice(proposal([("Bill", 1)], due_date="2026-11-06", payment_term_id="2"), customer_id="9",
                      charges=[c1], fee_items=[], sales_term_id="77").payload
    assert p["SalesTermRef"] == {"value": "77"}
    p = build_invoice(proposal([("Bill", 1)], due_date="2026-11-06"), customer_id="9", charges=[c1],
                      fee_items=[]).payload
    assert "SalesTermRef" not in p
