"""U-778d1 b1 -- canonical form + comparison (design u778d section 5 / 6.4). Pure."""
import copy
import json
from decimal import Decimal
from pathlib import Path

from integrations.intuit.qbo.invoice_push.business.canonical import canonicalize, compare

FX = Path(__file__).parent / "fixtures" / "u778d1"


def load(name):
    return json.loads((FX / name).read_text())


def payload_with_due():
    p = load("linktest_invoice_payload.json")
    p["DueDate"] = "2026-11-06"  # QBO defaults DueDate to TxnDate when the payload omits it
    return p


def response():
    return load("linktest_create_response.json")["Invoice"]


def test_response_canonicalizes_equal_to_payload_names_and_subtotal_ignored():
    exp, act = canonicalize(payload_with_due()), canonicalize(response())
    assert exp.lines == act.lines
    assert compare(exp, act) == []
    assert len(act.lines) == 2  # SubTotalLineDetail dropped


def test_canonical_types_and_precision():
    c = canonicalize(response())
    l0 = c.lines[0]
    assert l0.amount == Decimal("32.50") and str(l0.amount) == "32.50"
    assert str(l0.qty) == "1.0000" and str(l0.unit_price) == "32.5000"
    assert l0.item_ref == "537" and l0.service_date == "2026-01-31"
    assert l0.linked_txn == (("68056", "ReimburseCharge", "1"),)
    assert c.total_amt == Decimal("48.75")
    assert c.lines[1].item_ref is None and c.lines[1].qty is None


def only(diffs, path_part):
    assert len(diffs) == 1, diffs
    assert path_part in diffs[0].path
    return diffs[0]


def mutated(fn):
    r = response()
    fn(r)
    return canonicalize(r)


def test_amount_change_is_one_diff():
    def f(r):
        r["Line"][0]["Amount"] = 32.51
        r["TotalAmt"] = 48.76
    d = only(compare(canonicalize(payload_with_due()), mutated(f)), "amount")
    assert d.expected == Decimal("32.50") and d.actual == Decimal("32.51")


def test_txnlineid_change_is_one_diff():
    def f(r):
        r["Line"][0]["LinkedTxn"][0]["TxnLineId"] = "2"
    only(compare(canonicalize(payload_with_due()), mutated(f)), "linked_txn")


def test_item_account_ref_change_is_one_diff():
    def f(r):
        r["Line"][0]["SalesItemLineDetail"]["ItemAccountRef"]["value"] = "7"
    only(compare(canonicalize(payload_with_due()), mutated(f)), "item_account_ref")


def test_markup_percent_change_is_one_diff():
    def f(r):
        r["Line"][0]["SalesItemLineDetail"]["MarkupInfo"]["Percent"] = 35.01
    d = only(compare(canonicalize(payload_with_due()), mutated(f)), "markup.percent")
    assert d.actual == Decimal("35.01")


def test_line_order_swap_is_one_diff():
    def f(r):
        r["Line"][0], r["Line"][1] = r["Line"][1], r["Line"][0]
    only(compare(canonicalize(payload_with_due()), mutated(f)), "order")


def test_missing_line_is_one_diff():
    def f(r):
        del r["Line"][1]
        r["TotalAmt"] = 32.5
    d = only(compare(canonicalize(payload_with_due()), mutated(f)), "line_count")
    assert (d.expected, d.actual) == (2, 1)


def test_extra_line_is_one_diff():
    def f(r):
        r["Line"].insert(2, copy.deepcopy(r["Line"][1]))
    d = only(compare(canonicalize(payload_with_due()), mutated(f)), "line_count")
    assert (d.expected, d.actual) == (2, 3)


def test_header_docnumber_is_one_diff():
    def f(r):
        r["DocNumber"] = "OTHER"
    only(compare(canonicalize(payload_with_due()), mutated(f)), "doc_number")


def test_other_header_fields_each_one_diff():
    for key, val, path in [("TxnDate", "2026-11-07", "txn_date"), ("DueDate", "2026-12-01", "due_date"),
                           ("PrivateNote", "x", "private_note"), ("CustomerRef", {"value": "9"}, "customer_ref"),
                           ("SalesTermRef", {"value": "2"}, "sales_term_ref")]:
        def f(r, key=key, val=val):
            r[key] = val
        only(compare(canonicalize(payload_with_due()), mutated(f)), path)


def test_header_linked_txn_difference_is_one_diff():
    def f(r):
        r["LinkedTxn"].append({"TxnId": "1", "TxnType": "ReimburseCharge"})
    only(compare(canonicalize(payload_with_due()), mutated(f)), "linked_txn")


def test_total_amt_only_difference_is_one_diff():
    def f(r):
        r["TotalAmt"] = 50.0
    only(compare(canonicalize(payload_with_due()), mutated(f)), "total_amt")


def test_violation_two_linked_txns_is_reported_not_dropped():
    def f(r):
        r["Line"][0]["LinkedTxn"].append({"TxnId": "5", "TxnType": "ReimburseCharge", "TxnLineId": "1"})
    diffs = compare(canonicalize(payload_with_due()), mutated(f))
    assert any("violation" in d.path for d in diffs)


def test_violation_wrong_linked_type_is_reported():
    def f(r):
        r["Line"][0]["LinkedTxn"][0]["TxnType"] = "Estimate"
    diffs = compare(canonicalize(payload_with_due()), mutated(f))
    assert any("violation" in d.path for d in diffs)


def test_line_id_checks_only_when_required():
    exp = canonicalize(payload_with_due())
    assert compare(exp, canonicalize(response()), require_line_ids=True) == []

    def dup(r):
        r["Line"][1]["Id"] = "1"
    diffs = compare(exp, mutated(dup), require_line_ids=True)
    assert len(diffs) == 1 and "line_id" in diffs[0].path

    def blank(r):
        del r["Line"][1]["Id"]
    diffs = compare(exp, mutated(blank), require_line_ids=True)
    assert len(diffs) == 1 and "line_id" in diffs[0].path
    assert compare(exp, mutated(blank)) == []


def test_credit_and_no_markup_lines_canonicalize_from_qbo_built_shapes():
    shapes = load("qbo_built_invoice_shapes.json")
    c = canonicalize(shapes)
    assert len(c.lines) == 6 and c.total_amt == Decimal("401846.57")
    assert c.lines[0].amount == Decimal("-15.34") and c.lines[0].qty == Decimal("-1.0000")
    assert c.lines[0].markup.percent_based is False and c.lines[0].markup.value == Decimal("-15.34")
    assert compare(c, canonicalize(copy.deepcopy(shapes))) == []


# ======================= fix round 1 (U-778d1 f1) =======================
def test_f4_currency_and_exchange_rate_differences_are_reported():
    exp = canonicalize(payload_with_due())
    r = response()
    r["CurrencyRef"] = {"value": "CAD"}
    r["ExchangeRate"] = 0.73
    paths = {d.path for d in compare(exp, canonicalize(r))}
    assert {"currency_ref", "exchange_rate"} <= paths


def test_f4_absent_currency_is_home_and_absent_rate_is_one():
    exp = canonicalize(payload_with_due())  # payload names no currency
    assert exp.currency_ref == "USD" and exp.exchange_rate == Decimal("1")
    r = response()
    assert r["CurrencyRef"]["value"] == "USD"
    assert compare(exp, canonicalize(r)) == []
    r.pop("CurrencyRef")
    assert compare(exp, canonicalize(r)) == []
    r["ExchangeRate"] = 1.0
    assert compare(exp, canonicalize(r)) == []
    r["CurrencyRef"] = {"value": "CAD"}
    r.pop("ExchangeRate")
    assert [d.path for d in compare(exp, canonicalize(r))] == ["currency_ref"]


def test_f6_actual_invoice_must_carry_total_amt():
    exp = canonicalize(payload_with_due())
    r = response()
    r.pop("TotalAmt")
    assert compare(exp, canonicalize(r)) == []  # legacy derivation still applies to a payload-style input
    diffs = compare(exp, canonicalize(r, require_total=True))
    assert diffs and any("TotalAmt" in str(d.actual) for d in diffs)
    assert canonicalize(payload_with_due()).total_amt == Decimal("48.75")  # payload-derived expectation OK
