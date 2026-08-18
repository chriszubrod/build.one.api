"""Pure tests for AIA G703 Continuation Sheet PDF renderer."""

import io
from decimal import Decimal

from pypdf import PdfReader

from entities.invoice.business.cover import _format_money
from entities.invoice.business.g703 import build_g703_pdf


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)


def _header(**overrides):
    base = {
        "application_no": "APP-2026-04",
        "application_date": "April 1, 2026",
        "period_to": "March 31, 2026",
        "architect_project_no": "ARCH-1001",
    }
    base.update(overrides)
    return base


def _line(**overrides):
    row = {
        "item_no": "1.0000",
        "description": "Site grading and excavation",
        "scheduled": Decimal("50000.00"),
        "prev": Decimal("30000.00"),
        "this_period": Decimal("10000.00"),
        "stored": Decimal("5000.00"),
        "total_to_date": Decimal("45000.00"),
        "pct": "90.00%",
        "balance": Decimal("5000.00"),
        "retainage": Decimal("4500.00"),
    }
    row.update(overrides)
    return row


def _grand(**overrides):
    base = {
        "scheduled": Decimal("150000.00"),
        "prev": Decimal("90000.00"),
        "this_period": Decimal("30000.00"),
        "stored": Decimal("15000.00"),
        "total_to_date": Decimal("135000.00"),
        "pct": "90.00%",
        "balance": Decimal("15000.00"),
        "retainage": Decimal("13500.00"),
    }
    base.update(overrides)
    return base


def test_g703_pdf_core_content():
    rows = [
        _line(),
        _line(
            item_no="2.0000",
            description="Concrete foundations",
            scheduled=Decimal("60000.00"),
            prev=Decimal("40000.00"),
            this_period=Decimal("15000.00"),
            stored=Decimal("5000.00"),
            total_to_date=Decimal("60000.00"),
            pct="100.00%",
            balance=Decimal("0.00"),
            retainage=Decimal("6000.00"),
        ),
        _line(
            item_no="3.0000",
            description="Framing labor",
            scheduled=Decimal("40000.00"),
            prev=Decimal("20000.00"),
            this_period=Decimal("5000.00"),
            stored=Decimal("5000.00"),
            total_to_date=Decimal("30000.00"),
            pct="75.00%",
            balance=Decimal("10000.00"),
            retainage=Decimal("3000.00"),
        ),
    ]
    grand = _grand()

    pdf = build_g703_pdf(_header(), rows, grand)
    assert pdf[:4] == b"%PDF"

    text = _pdf_text(pdf)
    assert "CONTINUATION SHEET" in text
    assert "Site grading and excavation" in text
    assert "APP-2026-04" in text
    assert "GRAND TOTALS" in text
    assert _format_money(Decimal("50000.00")) in text
    assert _format_money(grand["scheduled"]) in text


def test_g703_rows_assembles_and_reconciles():
    from decimal import Decimal
    from entities.invoice.business.g703 import build_g703_rows

    sov = [
        {"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("300000")},
        {"cost_code_number": "13", "cost_code_name": "Framing", "scheduled_value": Decimal("0")},
        {"cost_code_number": "90", "cost_code_name": "Builder's Fee", "scheduled_value": Decimal("50000")},
    ]
    draws = [
        {"label": "HA-01",
         "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("100000")}],
         "subtotal": Decimal("100000"), "builders_fee": Decimal("14000"), "total": Decimal("114000")},
        {"label": "HA-02",
         "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("50000")},
                        {"cost_code_number": "13", "cost_code_name": "Framing", "amount": Decimal("20000")}],
         "subtotal": Decimal("70000"), "builders_fee": Decimal("9800"), "total": Decimal("79800")},
    ]
    rows, grand = build_g703_rows(sov, draws, "HA-02")
    by = {r["item_no"]: r for r in rows}

    # Work code 06: C=300000, D(prior HA-01)=100000, E(current HA-02)=50000, G=150000, I=C-G
    assert by["06"]["scheduled"] == Decimal("300000")
    assert by["06"]["prev"] == Decimal("100000")
    assert by["06"]["this_period"] == Decimal("50000")
    assert by["06"]["total_to_date"] == Decimal("150000")
    assert by["06"]["balance"] == Decimal("150000")
    assert by["06"]["pct"] == "50.00%"
    # Over-budget-ish code 13: C=0 -> pct 0.00%, negative balance
    assert by["13"]["this_period"] == Decimal("20000")
    assert by["13"]["balance"] == Decimal("-20000")
    assert by["13"]["pct"] == "0.00%"
    # Fee line 90: C from budget, D = prior fee, E = current fee (NOT a work category)
    assert by["90"]["scheduled"] == Decimal("50000")
    assert by["90"]["prev"] == Decimal("14000")
    assert by["90"]["this_period"] == Decimal("9800")
    assert by["90"]["total_to_date"] == Decimal("23800")
    # Reconciliation: grand E == current draw total; grand D == prior draw totals; G = D+E
    assert grand["this_period"] == Decimal("79800")   # HA-02 total
    assert grand["prev"] == Decimal("114000")          # HA-01 total
    assert grand["total_to_date"] == Decimal("193800")


def test_g703_fee_line_resolves_by_number_prefix_or_name():
    from decimal import Decimal
    from entities.invoice.business.g703 import build_g703_rows

    # Fee SoV line numbered "90.01" (not exactly "90") must be treated as THE fee:
    # driving fee_C, and NEVER rendered as a phantom work row.
    sov = [
        {"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("100000")},
        {"cost_code_number": "90.01", "cost_code_name": "Builder's Fee", "scheduled_value": Decimal("50000")},
    ]
    draws = [
        {"label": "HA-01",
         "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("10000")}],
         "subtotal": Decimal("10000"), "builders_fee": Decimal("1400"), "total": Decimal("11400")},
    ]
    rows, _ = build_g703_rows(sov, draws, "HA-01")
    nums = [r["item_no"] for r in rows]
    assert nums.count("90.01") == 1          # exactly one fee row, no phantom work row
    fee = next(r for r in rows if r["item_no"] == "90.01")
    assert fee["scheduled"] == Decimal("50000")    # fee_C from the budget fee line
    assert fee["this_period"] == Decimal("1400")   # current draw's fee
    assert fee["description"] == "Builder's Fee"
    assert any(r["item_no"] == "06" for r in rows)  # work row present, not double-counted


def test_g703_rows_duplicate_label_disambiguated_by_date():
    """U-270: two CODED draws sharing the same label (adversarial-review finding 4).

    ``labels.index(current_label)`` (the pre-fix behavior) always resolves to the
    FIRST same-labeled draw — misclassifying it as "current" and dropping the true
    current draw's rollup entirely (it lands in neither the prior nor the current
    column). ``current_date`` must disambiguate: the current invoice's own date
    identifies which of the two same-labeled entries is "current"; the other is
    "prior".
    """
    from decimal import Decimal
    from entities.invoice.business.g703 import build_g703_rows

    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("300000")}]
    draws = [
        {"label": "HA-DUP", "date": "2026-01-01",
         "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("40000")}],
         "subtotal": Decimal("40000"), "builders_fee": Decimal("5600"), "total": Decimal("45600")},
        {"label": "HA-DUP", "date": "2026-02-01",
         "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("25000")}],
         "subtotal": Decimal("25000"), "builders_fee": Decimal("3500"), "total": Decimal("28500")},
    ]

    rows, grand = build_g703_rows(sov, draws, "HA-DUP", current_date="2026-02-01")
    by = {r["item_no"]: r for r in rows}

    # Earlier same-label draw (2026-01-01, $40000) -> PRIOR (col D).
    # Later same-label draw (2026-02-01, $25000, matches current_date) -> CURRENT (col E).
    assert by["06"]["prev"] == Decimal("40000")
    assert by["06"]["this_period"] == Decimal("25000")
    assert grand["prev"] == Decimal("45600")        # earlier draw's total (incl. fee)
    assert grand["this_period"] == Decimal("28500")  # current draw's total (incl. fee)


def test_g703_rows_duplicate_label_and_date_falls_back_deterministically():
    """Deeper degenerate case: two draws tied on BOTH label AND date (a literal
    duplicate row — draws carries no further identity to break the tie). Documented,
    deliberate fallback to the first match: must not raise, and must be deterministic
    (not e.g. depend on dict ordering)."""
    from decimal import Decimal
    from entities.invoice.business.g703 import build_g703_rows

    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("100000")}]
    draws = [
        {"label": "HA-05", "date": "2026-03-01",
         "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("10000")}],
         "subtotal": Decimal("10000"), "builders_fee": Decimal("1400"), "total": Decimal("11400")},
        {"label": "HA-05", "date": "2026-03-01",
         "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("20000")}],
         "subtotal": Decimal("20000"), "builders_fee": Decimal("2800"), "total": Decimal("22800")},
    ]

    rows, grand = build_g703_rows(sov, draws, "HA-05", current_date="2026-03-01")
    by = {r["item_no"]: r for r in rows}
    assert by["06"]["prev"] == Decimal("0")            # first match (index 0) treated as current
    assert by["06"]["this_period"] == Decimal("10000")
    assert grand["this_period"] == Decimal("11400")


def test_g703_rows_current_absent_yields_zero_this_period():
    from decimal import Decimal
    from entities.invoice.business.g703 import build_g703_rows

    sov = [{"cost_code_number": "06", "cost_code_name": "Grading", "scheduled_value": Decimal("100000")}]
    draws = [{"label": "HA-01",
              "categories": [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("10000")}],
              "subtotal": Decimal("10000"), "builders_fee": Decimal("0"), "total": Decimal("10000")}]
    rows, grand = build_g703_rows(sov, draws, "HA-99")  # current_label not in draws
    assert grand["this_period"] == Decimal("0")
    assert grand["prev"] == Decimal("10000")
