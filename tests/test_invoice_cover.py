"""U-191 — invoice cover rollup (pure). The cover PDF renderer was retired in
U-204 (replaced by the Draw Request page); only the rollup remains here."""

from decimal import Decimal

from entities.invoice.business.cover import build_cover_rollup


def _line(
    *,
    source_type="BillLineItem",
    cost_code_number="100",
    cost_code_name="Site",
    billed_price=100.0,
    price=None,
    amount=None,
    is_credit=False,
):
    row = {
        "source_type": source_type,
        "cost_code_number": cost_code_number,
        "cost_code_name": cost_code_name,
        "billed_price": billed_price,
        "is_credit": is_credit,
    }
    if price is not None:
        row["price"] = price
    if amount is not None:
        row["amount"] = amount
    return row


def test_rollup_single_cost_code_sums_lines():
    lines = [
        _line(billed_price=100.0),
        _line(billed_price=50.25),
    ]
    model = build_cover_rollup(lines, fee_rate=None)
    assert len(model.categories) == 1
    assert model.categories[0].amount == Decimal("150.25")
    assert model.subtotal == Decimal("150.25")
    assert model.builders_fee == Decimal("0")
    assert model.total == Decimal("150.25")
    assert model.fee_rate is None


def test_rollup_multi_cost_code_sorted_by_number():
    lines = [
        _line(cost_code_number="200", cost_code_name="Structure", billed_price=10),
        _line(cost_code_number="100", cost_code_name="Site", billed_price=5),
        _line(cost_code_number="200", cost_code_name="Structure", billed_price=2),
    ]
    model = build_cover_rollup(lines, fee_rate=Decimal("0.1"))
    assert [c.cost_code_number for c in model.categories] == ["100", "200"]
    assert model.categories[0].amount == Decimal("5")
    assert model.categories[1].amount == Decimal("12")
    assert model.subtotal == Decimal("17")
    assert model.builders_fee == Decimal("1.70")
    assert model.total == Decimal("18.70")


def test_rollup_bill_credit_negates_positive_price():
    lines = [_line(source_type="BillCreditLineItem", billed_price=None, price=250.0)]
    model = build_cover_rollup(lines, fee_rate=None)
    assert model.subtotal == Decimal("-250")


def test_rollup_expense_credit_negates_when_is_credit():
    lines = [
        _line(
            source_type="ExpenseLineItem",
            billed_price=80.0,
            is_credit=True,
        )
    ]
    model = build_cover_rollup(lines, fee_rate=None)
    assert model.subtotal == Decimal("-80")


def test_rollup_billed_price_fallback_to_price():
    lines = [_line(billed_price=None, price=33.33)]
    model = build_cover_rollup(lines, fee_rate=None)
    assert model.subtotal == Decimal("33.33")


def test_rollup_fee_rounds_to_cents():
    lines = [_line(billed_price=100.0)]
    model = build_cover_rollup(lines, fee_rate=Decimal("0.3333"))
    assert model.builders_fee == Decimal("33.33")
    assert model.total == Decimal("133.33")


def test_rollup_skips_manual_lines():
    lines = [
        _line(billed_price=100.0),
        {"source_type": "Manual", "cost_code_number": "999", "billed_price": 500.0},
    ]
    model = build_cover_rollup(lines, fee_rate=None)
    assert model.subtotal == Decimal("100")


def test_rollup_groups_by_cost_code_number_only():
    """Two lines share a cost-code number but carry different names (one blank) —
    they must land in ONE category (matching the expanded TOC's number-only
    grouping), labelled with the first non-empty name."""
    lines = [
        _line(cost_code_number="100", cost_code_name="", billed_price=40),
        _line(cost_code_number="100", cost_code_name="Site", billed_price=60),
    ]
    model = build_cover_rollup(lines, fee_rate=None)
    assert len(model.categories) == 1
    assert model.categories[0].cost_code_number == "100"
    assert model.categories[0].cost_code_name == "Site"
    assert model.categories[0].amount == Decimal("100")


def test_rollup_reconciles_to_expanded_toc_signed_amounts():
    """The cover's Decimal subtotal must equal the expanded TOC's own float
    `_toc_signed_amount` sum at cent precision — same rows, same sign rules — so
    page 1 and the expanded TOC never disagree by a penny."""
    from entities.invoice.api.router import _toc_signed_amount

    lines = [
        _line(cost_code_number="100", billed_price=123.45),
        _line(cost_code_number="200", billed_price=67.89),
        _line(source_type="BillCreditLineItem", cost_code_number="200",
              billed_price=None, price=10.00),
        _line(source_type="ExpenseLineItem", cost_code_number="300",
              billed_price=5.55, is_credit=True),
        {"source_type": "Manual", "cost_code_number": "999", "billed_price": 500.0},
    ]
    model = build_cover_rollup(lines, fee_rate=None)
    toc_total = round(sum((_toc_signed_amount(r) or 0.0) for r in lines
                          if r.get("source_type") != "Manual"), 2)
    assert float(model.subtotal) == toc_total


def test_rollup_keeps_none_source_line_like_the_toc():
    """A None-source anomaly line is kept by the packet's `!= "Manual"` filter, so
    the cover keeps it too (grouped under a blank cost code) rather than silently
    dropping it and under-reporting the subtotal versus the TOC."""
    lines = [
        _line(billed_price=100.0),
        {"source_type": None, "cost_code_number": "", "billed_price": 25.0},
    ]
    model = build_cover_rollup(lines, fee_rate=None)
    assert model.subtotal == Decimal("125")
