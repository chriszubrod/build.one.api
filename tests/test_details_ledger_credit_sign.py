"""DETAILS column-N: credit sign + Price→Amount fallback (MR2-MAIN-10, 2026-09-11).

An `Expense.IsCredit` refund was written to the DETAILS ledger as a POSITIVE
column-N value on both sync paths — `is_credit` only changed the column-M type
label. A draw that tags such a row picks up +$X where the invoice charges −$X,
so the ledger overstates by 2×. MR2-MAIN-10 carried three credit rows
(−$5,690.00 sauna, −$223.72 and −$190.80 sconces): the draw would have read
$62,618.10 against a correct $50,409.06 — a $12,209.04 overstatement — while
the packet, which signs correctly via `cover._signed_line_amount`, read right.
That divergence is invisible to an outbox `done` status (KI-46).

The MS and Box builders are two physical copies of one logical ledger (KI-39),
so both sides go through `shared.api.money.details_ledger_amount`.
"""

from decimal import Decimal
from types import SimpleNamespace

from shared.api.money import details_ledger_amount


# ---------------------------------------------------------------- the seam


def test_credit_line_is_negated():
    assert details_ledger_amount(Decimal("5690.00"), None, is_credit=True) == Decimal("-5690.00")


def test_non_credit_line_is_untouched():
    assert details_ledger_amount(Decimal("5690.00"), None, is_credit=False) == Decimal("5690.00")


def test_negation_is_idempotent_not_an_unconditional_flip():
    """The U-344 landmine: credit amounts are moving to signed-negative at the
    write site. An already-negative value must pass through UNCHANGED, or the
    row flips positive the moment those writes land."""
    assert details_ledger_amount(Decimal("-223.72"), None, is_credit=True) == Decimal("-223.72")


def test_price_falls_back_to_amount():
    """KI-16: QBO-pulled account-based lines carry no Price. Without the
    fallback the row lands at N=0 and col-Z idempotency freezes it there."""
    assert details_ledger_amount(None, Decimal("180.99"), is_credit=False) == Decimal("180.99")


def test_amount_fallback_is_signed_too():
    assert details_ledger_amount(None, Decimal("223.72"), is_credit=True) == Decimal("-223.72")


def test_zero_survives_and_is_not_dropped():
    """`Decimal(0)` is falsy — a truthy guard would turn a genuine $0.00 into
    None and then into a skip."""
    assert details_ledger_amount(Decimal("0"), None, is_credit=True) == Decimal("0")


def test_both_none_is_zero_not_none():
    """A ledger cell can only mean a number. Returning Optional here would make
    every caller re-decide the same question and invite a truthiness guard."""
    assert details_ledger_amount(None, None, is_credit=False) == Decimal("0")


def test_returns_decimal_never_float():
    """CLAUDE.md: money is Decimal end to end; a float round-trip corrupts."""
    assert isinstance(details_ledger_amount(0.1, None, is_credit=False), Decimal)
    assert details_ledger_amount("3468.45", None, is_credit=False) == Decimal("3468.45")


# ------------------------------------------------------- Box row builder


def _expense(is_credit):
    return SimpleNamespace(
        id=1, public_id="e-pub", vendor_id=7, expense_date="2026-04-17",
        reference_number="QBO-69339", is_credit=is_credit,
    )


def _line(price, amount=None, public_id="li-pub"):
    return SimpleNamespace(
        id=12696, public_id=public_id, price=price, amount=amount,
        description="Sauna Equipment", sub_cost_code_id=253, project_id=93,
    )


def _build_box_expense_rows(monkeypatch, expense, line_items):
    """Drive row_builder's expense branch with its lazy service imports stubbed."""
    import entities.expense.business.service as expense_service
    import entities.expense_line_item.business.service as eli_service
    import entities.sub_cost_code.business.service as scc_service
    import entities.vendor.business.service as vendor_service
    import integrations.box.excel.business.row_builder as rb

    monkeypatch.setattr(
        expense_service, "ExpenseService",
        lambda: SimpleNamespace(read_by_public_id=lambda **kw: expense),
    )
    monkeypatch.setattr(
        eli_service, "ExpenseLineItemService",
        lambda: SimpleNamespace(read_by_expense_id=lambda **kw: line_items),
    )
    monkeypatch.setattr(
        vendor_service, "VendorService",
        lambda: SimpleNamespace(read_by_id=lambda **kw: SimpleNamespace(name="Accurate Industries")),
    )
    monkeypatch.setattr(
        scc_service, "SubCostCodeService",
        lambda: SimpleNamespace(read_by_id=lambda **kw: SimpleNamespace(number="32.4")),
    )
    return rb.build_details_rows("expense", expense.public_id, project_id=93)


BOX_N = 13
BOX_TYPE = 12


def test_box_row_negates_a_credit(monkeypatch):
    rows = _build_box_expense_rows(
        monkeypatch, _expense(True), [_line(Decimal("5690.00"))]
    )
    assert len(rows) == 1
    assert rows[0][BOX_N] == Decimal("-5690.00")
    assert rows[0][BOX_TYPE] == "Expense Credit"


def test_box_row_leaves_a_charge_positive(monkeypatch):
    rows = _build_box_expense_rows(
        monkeypatch, _expense(False), [_line(Decimal("5690.00"))]
    )
    assert rows[0][BOX_N] == Decimal("5690.00")
    assert rows[0][BOX_TYPE] == "Expense"


def test_box_row_uses_amount_when_price_is_null(monkeypatch):
    rows = _build_box_expense_rows(
        monkeypatch, _expense(True), [_line(None, amount=Decimal("223.72"))]
    )
    assert rows[0][BOX_N] == Decimal("-223.72")


# ---------------------------------------------- the MR2-MAIN-10 arithmetic


def test_draw_ledger_total_matches_the_invoice_side():
    """The three MR2-MAIN-10 credits, summed the way a draw's
    `SUMIFS(N:N, H:H, "<draw>")` would."""
    charges = Decimal("56513.58")
    credits = [Decimal("5690.00"), Decimal("223.72"), Decimal("190.80")]

    signed = charges + sum(
        details_ledger_amount(c, None, is_credit=True) for c in credits
    )
    unsigned_bug = charges + sum(credits)

    assert signed == Decimal("50409.06")
    assert unsigned_bug - signed == Decimal("12209.04")


# ------------------------------------------- MS-side call-site regression guard


def test_ms_expense_row_builders_route_through_the_shared_seam():
    """`ExpenseService.sync_to_excel_workbook` and its batch sibling build their
    column-N value inline, mid-Graph-call, so there is no seam to drive from a
    pure-logic test. Guard the call sites at the source level instead: both must
    go through `details_ledger_amount`, and neither may reconstruct the old
    unsigned `float(<line>.price) if ... else 0` expression that shipped the
    MR2-MAIN-10 overstatement.
    """
    import re
    from pathlib import Path

    import entities.expense.business.service as svc

    source = Path(svc.__file__).read_text()

    # Both known sync paths — single-expense and batch — go through the seam.
    # `>=`, not `==`: a correctly-written third path must not fail this test,
    # and a path that FORGETS the seam is caught by the regex below, not by a
    # count that such a path leaves untouched.
    assert source.count("details_ledger_amount(") >= 2

    unsigned = re.compile(r"float\(\s*\w+(?:\.\w+)*\.price\s*\)\s*if\b[^\n]*\belse\s*0")
    reintroduced = unsigned.search(source)
    assert reintroduced is None, (
        f"unsigned column-N expression reintroduced: {reintroduced.group(0)!r}"
    )
