"""Pure-logic tests for U-242 ReimburseCharge source fingerprint matching."""
from decimal import Decimal

from integrations.intuit.qbo.reimburse_charge.business.fingerprint import (
    RcBaseLine,
    SourceCandidate,
    build_candidate_index,
    classify_reimburse_line,
    match_outcome,
    parse_rc_base_lines,
    tier_match,
    tier_match_indexed,
)


def _rc_line(**overrides) -> RcBaseLine:
    defaults = {
        "rc_id": "rc-1",
        "customer_ref_value": "cust-1",
        "amount": Decimal("100.00"),
        "txn_date": "2026-08-01",
        "description": "Labor",
        "item_ref_value": "item-1",
        "has_been_invoiced": False,
    }
    defaults.update(overrides)
    return RcBaseLine(**defaults)


def _candidate(**overrides) -> SourceCandidate:
    defaults = {
        "source_type": "BillLineItem",
        "customer_ref_value": "cust-1",
        "amount": Decimal("100.00"),
        "description": "Labor",
        "txn_date": "2026-08-01",
        "item_ref_value": "item-1",
        "doc_number": "B-100",
        "vendor_or_entity_name": "Vendor A",
        "qbo_line_id": 1,
        "mapped_dbo_id": 42,
    }
    defaults.update(overrides)
    return SourceCandidate(**defaults)


def test_tier_a_ambiguous_tier_c_unique():
    """Same customer+amount+date but different description/item — B/C add discrimination."""
    rc_line = _rc_line()
    candidates = [
        _candidate(description="Labor", item_ref_value="item-1", qbo_line_id=1),
        _candidate(description="Materials", item_ref_value="item-2", qbo_line_id=2),
    ]

    tier_a = tier_match(rc_line, candidates, "A")
    tier_b = tier_match(rc_line, candidates, "B")
    tier_c = tier_match(rc_line, candidates, "C")

    assert match_outcome(tier_a) == "ambiguous"
    assert match_outcome(tier_b) == "unique"
    assert match_outcome(tier_c) == "unique"
    assert tier_c[0].qbo_line_id == 1


def test_genuinely_unmatched():
    rc_line = _rc_line(customer_ref_value="cust-missing")
    candidates = [_candidate(customer_ref_value="cust-other")]

    for tier in ("A", "B", "C"):
        assert match_outcome(tier_match(rc_line, candidates, tier)) == "unmatched"


def test_markup_line_excluded_before_matching():
    raw_rc = {
        "Id": "99",
        "CustomerRef": {"value": "cust-1", "name": "Customer A"},
        "TxnDate": "2026-08-01",
        "HasBeenInvoiced": False,
        "Line": [
            {
                "DetailType": "ReimburseLineDetail",
                "Amount": "50.00",
                "Description": "Markup",
                "ReimburseLineDetail": {
                    "ItemAccountRef": {"value": "1", "name": "Markup"},
                    "MarkupInfo": {"Percent": 10},
                },
            }
        ],
    }

    assert classify_reimburse_line(raw_rc["Line"][0]) == "derivative"
    base_lines, derivative_count, skipped = parse_rc_base_lines(raw_rc)
    assert base_lines == []
    assert derivative_count == 1
    assert skipped == 0

    # Matcher never sees derivative lines
    candidates = [_candidate(amount=Decimal("50.00"))]
    assert tier_match(_rc_line(amount=Decimal("50.00")), candidates, "A") == candidates
    assert not base_lines


def test_base_line_parsed_with_item_ref():
    raw_rc = {
        "Id": "100",
        "CustomerRef": {"value": "cust-1", "name": "Customer A"},
        "TxnDate": "2026-08-01T00:00:00-07:00",
        "HasBeenInvoiced": True,
        "Line": [
            {
                "DetailType": "ReimburseLineDetail",
                "Amount": "123.45",
                "Description": "Tile install",
                "ReimburseLineDetail": {
                    "ItemRef": {"value": "item-9", "name": "Tile"},
                },
            }
        ],
    }

    base_lines, derivative_count, skipped = parse_rc_base_lines(raw_rc)
    assert derivative_count == 0
    assert skipped == 0
    assert len(base_lines) == 1
    assert base_lines[0].amount == Decimal("123.45")
    assert base_lines[0].txn_date == "2026-08-01"
    assert base_lines[0].item_ref_value == "item-9"
    assert base_lines[0].has_been_invoiced is True


def test_parse_rc_base_lines_quantizes_amount_tier_paths_agree():
    """Over-precise QBO Amount is quantized at parse so index and scan paths agree."""
    raw_rc = {
        "Id": "101",
        "CustomerRef": {"value": "cust-1", "name": "Customer A"},
        "TxnDate": "2026-08-01",
        "HasBeenInvoiced": False,
        "Line": [
            {
                "DetailType": "ReimburseLineDetail",
                "Amount": "100.009",
                "Description": "Labor",
                "ReimburseLineDetail": {
                    "ItemRef": {"value": "item-1", "name": "Labor Item"},
                },
            }
        ],
    }

    base_lines, derivative_count, skipped = parse_rc_base_lines(raw_rc)
    assert derivative_count == 0
    assert skipped == 0
    assert len(base_lines) == 1
    assert base_lines[0].amount == Decimal("100.01")

    candidate = _candidate(amount=Decimal("100.00"))
    candidates = [candidate]
    index = build_candidate_index(candidates)
    rc_line = base_lines[0]

    for tier in ("A", "B", "C"):
        linear = tier_match(rc_line, candidates, tier)
        indexed = tier_match_indexed(rc_line, index, tier)
        assert linear == indexed
        assert linear == []


def test_tier_match_indexed_agrees_with_tier_match():
    """Indexed path must return the same matches as the linear scan."""
    rc_line = _rc_line()
    candidates = [
        _candidate(description="Labor", item_ref_value="item-1", qbo_line_id=1),
        _candidate(description="Materials", item_ref_value="item-2", qbo_line_id=2),
    ]
    index = build_candidate_index(candidates)

    for tier in ("A", "B", "C"):
        linear = tier_match(rc_line, candidates, tier)
        indexed = tier_match_indexed(rc_line, index, tier)
        assert {c.qbo_line_id for c in linear} == {c.qbo_line_id for c in indexed}
