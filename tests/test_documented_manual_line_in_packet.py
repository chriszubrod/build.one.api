"""A Manual invoice line carrying its own document belongs in the packet.

Manual lines are excluded from the packet by design — they are internal
accounting adjustments (offsets, reversals, and the markup siblings QBO splits
out of a labor charge), and the customer reads the TOC to check each charge
against a source document.

That reasoning stops applying when the Manual line has evidence of its own, via
`dbo.InvoiceLineItemAttachment`. Two real shapes produce such a line:

* a **QBO Deposit-sourced refund** — Deposits are not pulled at all, so the
  credit can never link to a local source (MR2-MAIN-10: −$4,917.73);
* **labor whose contract-labor bill was deleted** — the charge is real and the
  bill PDF still exists, but there is no local parent row left to hang it from
  (MR2-MAIN-10: three Selvin period roll-ups totalling $3,937.50).

Excluding those cost more than the missing backup pages: the amount stayed in
the invoice total while dropping out of the cover rollup, so the Draw Request
footed to a different number than the invoice. MR2-MAIN-10 read $46,471.54
against an invoice of $45,491.31 — exactly the $980.23 those four lines netted
to — and nothing on the page said so.

`is_packet_row` is now the single definition, shared by the router's `toc_items`
and `build_cover_rollup`, so the TOCs, the merged pages and the cover subtotal
cannot disagree about membership.
"""

from decimal import Decimal

from entities.invoice.business.cover import build_cover_rollup, is_packet_row


def _row(source_type, amount, attachment="", billed_price=None, cc="48.00"):
    return {
        "source_type": source_type,
        "amount": amount,
        "billed_price": billed_price,
        "attachment_public_id": attachment,
        "cost_code_number": cc,
        "cost_code_name": "Countertops",
    }


# ------------------------------------------------------------ the predicate


def test_source_linked_line_is_always_in():
    assert is_packet_row(_row("BillLineItem", 100.00)) is True


def test_bare_manual_line_is_out():
    """The markup siblings and $0-net offsets: no document, no page, no rollup."""
    assert is_packet_row(_row("Manual", 25.00)) is False


def test_documented_manual_line_is_in():
    assert is_packet_row(_row("Manual", -4917.73, attachment="att-pub-id")) is True


def test_empty_and_whitespace_attachment_do_not_count_as_a_document():
    """`enrich_line_items` defaults the field to "" — that must not open the gate."""
    assert is_packet_row(_row("Manual", 25.00, attachment="")) is False
    assert is_packet_row(_row("Manual", 25.00, attachment="   ")) is False


def test_missing_attachment_key_is_treated_as_no_document():
    assert is_packet_row({"source_type": "Manual", "amount": 25.00}) is False


def test_source_linked_line_needs_no_document_to_qualify():
    """A source line's document is checked by the Step 5 coverage gate, not here;
    dropping it from the packet over a missing attachment would hide the charge."""
    assert is_packet_row(_row("ExpenseLineItem", 100.00, attachment="")) is True


# ------------------------------------------------- the rollup it feeds


def test_documented_manual_lines_make_the_rollup_foot_to_the_invoice():
    """MR2-MAIN-10's actual shape, reduced: source lines plus three documented
    labor roll-ups and one documented refund foot to the QBO invoice total."""
    rows = [
        _row("BillLineItem", 41334.26, billed_price=41334.26, cc="01.00"),
        _row("ExpenseLineItem", 5137.28, billed_price=5137.28, cc="26.00"),
        _row("Manual", 525.00, attachment="a1", cc="65.02"),
        _row("Manual", 1575.00, attachment="a2", cc="65.02"),
        _row("Manual", 1837.50, attachment="a3", cc="65.02"),
        _row("Manual", -4917.73, attachment="a4", cc="48.00"),
        _row("Manual", 2070.58),  # markup siblings: no document, excluded
    ]
    rollup = build_cover_rollup(rows, None)
    assert rollup.subtotal == Decimal("45491.31")


def test_undocumented_manual_lines_alone_reproduce_the_980_23_gap():
    """Same rows with the four documents removed: the old behaviour, and the
    residual is exactly what the packet over-reported."""
    rows = [
        _row("BillLineItem", 41334.26, billed_price=41334.26, cc="01.00"),
        _row("ExpenseLineItem", 5137.28, billed_price=5137.28, cc="26.00"),
        _row("Manual", 525.00, cc="65.02"),
        _row("Manual", 1575.00, cc="65.02"),
        _row("Manual", 1837.50, cc="65.02"),
        _row("Manual", -4917.73, cc="48.00"),
    ]
    rollup = build_cover_rollup(rows, None)
    assert rollup.subtotal == Decimal("46471.54")
    assert rollup.subtotal - Decimal("45491.31") == Decimal("980.23")


def test_documented_credit_keeps_its_negative_sign_in_the_rollup():
    rows = [
        _row("BillLineItem", 10000.00, billed_price=10000.00),
        _row("Manual", -4917.73, attachment="a4"),
    ]
    assert build_cover_rollup(rows, None).subtotal == Decimal("5082.27")


# --------------------------------------------- the two surfaces agree


def test_router_and_cover_share_one_membership_rule():
    """The router's `toc_items` and `build_cover_rollup` must select the SAME
    rows — the packet's reconciliation invariant depends on it, and it used to
    be maintained by two hand-copied predicates."""
    import inspect

    from entities.invoice.api import router as router_mod

    assert router_mod.is_packet_row is is_packet_row
    src = inspect.getsource(router_mod._generate_invoice_packet)
    assert "is_packet_row(r)" in src, "toc_items must use the shared predicate"
    assert 'source_type") != "Manual"' not in src, (
        "the inline Manual filter is back — the two surfaces can drift again"
    )


def test_draw_financials_does_not_reintroduce_its_own_copy():
    """`DrawFinancialsService._coded_rollup` feeds the AIA and Trend pages of the
    SAME packet. It carried a third hand-copy of the Manual filter, which it
    applied BEFORE handing rows to `build_cover_rollup` — so the rollup's own
    predicate was overruled and one PDF reported two totals for one draw. It must
    pass enriched rows straight through."""
    import inspect

    from entities.invoice.business.draw_financials import DrawFinancialsService

    src = inspect.getsource(DrawFinancialsService._coded_rollup)
    assert 'source_type") != "Manual"' not in src, (
        "the pre-filter is back — AIA/Trend will drift from the Draw Request again"
    )
    assert "build_cover_rollup(enrich_line_items(line_items)" in src


# ------------------------------------- a Manual row's amount is NOT its `price`

def test_manual_row_ignores_price_and_uses_amount():
    """`price` on a QBO-pulled Manual line is the QBO **unit** price (the pull
    connector sets it from `unit_price` whenever the line has a qty); `amount` is
    the extended `Line.Amount`. Reading `price` prints a per-unit figure as the
    line total. This tier was unreachable for Manual rows until they became
    packet-eligible."""
    row = _row("Manual", 3937.50, attachment="a1")
    row["price"] = 1125.00  # unit price, qty 3.5
    assert build_cover_rollup([row], None).subtotal == Decimal("3937.50")


def test_manual_credit_keeps_its_sign_despite_a_positive_unit_price():
    """QBO states a credit as qty −1 × a POSITIVE unit price, so the `price` tier
    would drop the minus sign — and no negation rescues it, because the row is
    `Manual`, not `BillCreditLineItem`. The corpus holds a −$240,613.79 credit
    carrying price +$240,613.79."""
    row = _row("Manual", -240613.79, attachment="a1")
    row["price"] = 240613.79
    assert build_cover_rollup([row], None).subtotal == Decimal("-240613.79")


def test_source_linked_row_still_uses_price_when_billed_price_is_absent():
    """The skip is Manual-only — BillCredit and EmployeeLabor rows have no
    marked-up Price column and legitimately fall through to `price`."""
    row = _row("BillCreditLineItem", 999.99, attachment="")
    row["price"] = 250.00
    assert build_cover_rollup([row], None).subtotal == Decimal("-250.00")
