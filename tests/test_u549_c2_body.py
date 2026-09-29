# Python Standard Library Imports
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

# Local Imports
from entities.ramp_chaser_digest.business.body import (
    AGED_MARKER_THRESHOLD_DAYS,
    RAMP_CHASER_DIGEST_BODY_TYPE,
    render_digest,
)

_CHI = ZoneInfo("America/Chicago")


def _item(**kwargs):
    base = {
        "merchant_name": "Merchant",
        "amount": Decimal("10.00"),
        "transaction_date": "2026-09-15",
        "needs_memo": False,
        "needs_receipt": True,
        "first_seen_at": "2026-09-20T12:00:00.000",
    }
    base.update(kwargs)
    return base


def _render(items, *, now=None, first_name="Cassidy", card_holder="Cassidy Cardholder"):
    now = now or datetime(2026, 9, 28, 15, 0, 0, tzinfo=_CHI)
    return render_digest(
        first_name=first_name,
        card_holder=card_holder,
        items=items,
        now=now,
        tz=_CHI,
    )


def test_body_type_is_plain_text_for_outbox():
    assert RAMP_CHASER_DIGEST_BODY_TYPE == "Text"


def test_three_sections_in_order_and_partitioning():
    items = [
        _item(
            merchant_name="Lowe's",
            amount=Decimal("92.50"),
            transaction_date="2026-09-18",
            needs_memo=True,
            needs_receipt=True,
            first_seen_at="2026-08-05T00:00:00.000",
        ),
        _item(
            merchant_name="Home Depot",
            amount=Decimal("184.22"),
            transaction_date="2026-09-12",
            needs_memo=False,
            needs_receipt=True,
            first_seen_at="2026-09-12T00:00:00.000",
        ),
        _item(
            merchant_name="Tractor Supply",
            amount=Decimal("310.00"),
            transaction_date="2026-09-24",
            needs_memo=True,
            needs_receipt=False,
            first_seen_at="2026-09-24T00:00:00.000",
        ),
    ]
    _, body = _render(items)

    rm = body.index("Receipt & Memo")
    ro = body.index("Receipt Only")
    mo = body.index("Memo Only")
    assert rm < ro < mo
    assert "Lowe's" in body
    assert "Home Depot" in body
    assert "Tractor Supply" in body
    assert body.count("Receipt & Memo") == 1


def test_empty_sections_omitted():
    items = [
        _item(
            merchant_name="Memo Co",
            needs_memo=True,
            needs_receipt=False,
        ),
    ]
    _, body = _render(items)
    assert "Memo Only" in body
    assert "Receipt & Memo" not in body
    assert "Receipt Only" not in body


def test_aged_marker_uses_first_seen_not_transaction_date():
    # Swipe months ago; we only started chasing long enough ago to show the aged marker.
    items = [
        _item(
            merchant_name="Amazon",
            amount=Decimal("47.10"),
            transaction_date="2026-01-04",
            needs_memo=True,
            needs_receipt=True,
            first_seen_at="2026-09-10T12:00:00.000",
        ),
    ]
    now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=_CHI)
    _, body = _render(items, now=now)
    assert "← 18 days" in body
    assert "← 267 days" not in body


def test_subject_date_matches_business_tz_and_card_holder():
    now = datetime(2026, 9, 28, 1, 30, 0, tzinfo=_CHI)
    subject, _ = _render([], now=now, card_holder="Pat Example")
    assert subject == "Ramp Action Needed - 2026-09-28 - Pat Example"


def test_none_amount_renders_without_crash():
    items = [_item(amount=None, merchant_name="No Amount Shop")]
    _, body = _render(items)
    line = [ln for ln in body.splitlines() if "No Amount Shop" in ln][0]
    assert "No Amount Shop" in line
    assert "$" not in line


def test_zero_amount_renders_dollars():
    items = [_item(amount=Decimal("0.00"), merchant_name="Zero Cafe")]
    _, body = _render(items)
    assert "$0.00" in body


def test_negative_refund_amount():
    items = [_item(amount=Decimal("-47.10"), merchant_name="Refund Mart")]
    _, body = _render(items)
    assert "-$47.10" in body


def test_four_figure_amount_thousands_separator():
    items = [_item(amount=Decimal("3884.98"), merchant_name="Big Ticket")]
    _, body = _render(items)
    assert "$3,884.98" in body


def test_z_suffix_transaction_date_and_naive_first_seen_no_type_error():
    items = [
        _item(
            transaction_date="2026-09-01T12:00:00Z",
            first_seen_at="2026-09-27T21:01:00.123",
            merchant_name="UTC Swipe",
        ),
    ]
    now = datetime(2026, 9, 28, 10, 0, 0, tzinfo=_CHI)
    subject, body = _render(items, now=now)
    assert "Sep 01" in body
    assert "← " not in body
    assert subject.endswith("Cassidy Cardholder")


def test_date_only_accounting_transaction_date():
    items = [_item(transaction_date="2026-08-04", merchant_name="Accounting Day")]
    _, body = _render(items)
    assert "Aug 04" in body


def test_unparseable_transaction_date_degrades_row():
    items = [_item(transaction_date="not-a-valid-date", merchant_name="Broken Date Co")]
    _, body = _render(items)
    assert "Broken Date Co" in body
    line = [ln for ln in body.splitlines() if "Broken Date Co" in ln][0]
    assert "?" in line


def test_long_merchant_name_keeps_amount_on_same_line():
    long_name = "A" * 120
    items = [_item(merchant_name=long_name, amount=Decimal("12.34"))]
    _, body = _render(items)
    line = [ln for ln in body.splitlines() if long_name in ln][0]
    assert "$12.34" in line
    assert long_name in line


def test_apostrophe_and_ampersand_merchant_plain_text():
    items = [
        _item(merchant_name="Lowe's", needs_memo=True, needs_receipt=True),
        _item(
            merchant_name="AT&T",
            needs_memo=False,
            needs_receipt=True,
            transaction_date="2026-09-10",
        ),
    ]
    _, body = _render(items)
    assert "Lowe's" in body
    assert "AT&T" in body
    assert "<" not in body
    assert "&amp;" not in body


def test_fixed_column_whitespace_survives_plain_text_layout():
    items = [
        _item(
            merchant_name="Lowe's",
            amount=Decimal("92.50"),
            transaction_date="2026-09-18",
            needs_memo=True,
            needs_receipt=True,
            first_seen_at="2026-09-18T00:00:00.000",
        ),
    ]
    _, body = _render(items)
    line = [ln for ln in body.splitlines() if "Lowe's" in ln][0]
    assert "  Sep 18   Lowe's" in line
    gap = line[line.index("Lowe's") + len("Lowe's") : line.index("$92.50")]
    assert len(gap) >= 2 and gap.strip() == ""


def test_closing_exactly_thanks_chris_no_extra_paragraphs():
    _, body = _render([_item()])
    assert "easiest way" not in body.lower()
    assert "billing" not in body.lower()
    assert body.endswith("Thanks,\nChris")
    assert body.count("Thanks,") == 1


def test_greeting_and_intro():
    _, body = _render([_item()], first_name="Zach")
    assert body.startswith("Zach,\n\nWhen you have a moment")


def test_aged_marker_absent_at_threshold_days():
    now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=_CHI)
    chase_start = now.date().toordinal() - AGED_MARKER_THRESHOLD_DAYS
    first_seen = datetime.fromordinal(chase_start).strftime("%Y-%m-%dT12:00:00.000")
    items = [
        _item(
            merchant_name="Borderline Cafe",
            first_seen_at=first_seen,
            needs_memo=True,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items, now=now)
    assert "← " not in body


def test_aged_marker_present_above_threshold_days():
    now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=_CHI)
    chase_start = now.date().toordinal() - (AGED_MARKER_THRESHOLD_DAYS + 1)
    first_seen = datetime.fromordinal(chase_start).strftime("%Y-%m-%dT12:00:00.000")
    items = [
        _item(
            merchant_name="Stale Vendor",
            first_seen_at=first_seen,
            needs_memo=True,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items, now=now)
    assert f"← {AGED_MARKER_THRESHOLD_DAYS + 1} days" in body


def _amount_column_end_positions(body: str, merchants: list[str]) -> list[int]:
    lines = []
    for merchant in merchants:
        line = next(ln for ln in body.splitlines() if merchant in ln)
        lines.append(line)
    ends = []
    for line in lines:
        dollar = line.find("$")
        if dollar < 0:
            continue
        cents = line.index(".", dollar) + 3
        ends.append(cents)
    return ends


def test_amounts_right_align_decimal_column_in_section():
    items = [
        _item(
            merchant_name="Small",
            amount=Decimal("92.50"),
            transaction_date="2026-09-18",
            needs_memo=True,
            needs_receipt=True,
        ),
        _item(
            merchant_name="Large",
            amount=Decimal("3884.98"),
            transaction_date="2026-09-17",
            needs_memo=True,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items)
    ends = _amount_column_end_positions(body, ["Small", "Large"])
    assert len(ends) == 2
    assert ends[0] == ends[1]


def test_none_amount_does_not_break_amount_alignment():
    items = [
        _item(
            merchant_name="Has Amount",
            amount=Decimal("100.00"),
            needs_memo=True,
            needs_receipt=True,
        ),
        _item(
            merchant_name="Missing Amount",
            amount=None,
            needs_memo=True,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items)
    has_line = next(ln for ln in body.splitlines() if "Has Amount" in ln)
    none_line = next(ln for ln in body.splitlines() if "Missing Amount" in ln)
    assert "$100.00" in has_line
    assert "$" not in none_line
    amount_end = has_line.index(".", has_line.index("$")) + 3
    assert has_line[amount_end - 1] == "0"
    assert none_line == none_line.rstrip()
    assert none_line.endswith("Missing Amount")


def test_negative_amount_right_aligns_with_positive_in_section():
    items = [
        _item(
            merchant_name="Charge",
            amount=Decimal("92.50"),
            needs_memo=True,
            needs_receipt=True,
        ),
        _item(
            merchant_name="Refund",
            amount=Decimal("-47.10"),
            needs_memo=True,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items)
    ends = _amount_column_end_positions(body, ["Charge", "Refund"])
    assert len(ends) == 2
    assert ends[0] == ends[1]


def _merchant_column_start(body: str, merchant: str) -> int:
    line = next(ln for ln in body.splitlines() if merchant in ln)
    return line.index(merchant)


def test_amount_column_aligns_across_sections_in_one_render():
    items = [
        _item(
            merchant_name="Sherwin-Williams",
            amount=Decimal("3884.98"),
            transaction_date="2026-09-30",
            needs_memo=True,
            needs_receipt=True,
        ),
        _item(
            merchant_name="Home Depot",
            amount=Decimal("184.22"),
            transaction_date="2026-09-12",
            needs_memo=False,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items)
    ends = _amount_column_end_positions(body, ["Sherwin-Williams", "Home Depot"])
    assert len(ends) == 2
    assert ends[0] == ends[1]


def test_merchant_column_aligns_across_sections_in_one_render():
    items = [
        _item(
            merchant_name="Sherwin-Williams",
            amount=Decimal("3884.98"),
            transaction_date="2026-09-30",
            needs_memo=True,
            needs_receipt=True,
        ),
        _item(
            merchant_name="Home Depot",
            amount=Decimal("184.22"),
            transaction_date="2026-09-12",
            needs_memo=False,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items)
    assert _merchant_column_start(body, "Sherwin-Williams") == _merchant_column_start(
        body, "Home Depot"
    )


def test_no_rendered_line_ends_with_whitespace():
    items = [
        _item(
            merchant_name="Mystery Co",
            amount=None,
            needs_memo=True,
            needs_receipt=True,
        ),
        _item(
            merchant_name="Sherwin-Williams",
            amount=Decimal("3884.98"),
            transaction_date="2026-09-30",
            needs_memo=True,
            needs_receipt=True,
        ),
        _item(
            merchant_name="Home Depot",
            amount=Decimal("184.22"),
            transaction_date="2026-09-12",
            needs_memo=False,
            needs_receipt=True,
        ),
    ]
    _, body = _render(items)
    for line in body.splitlines():
        assert line == line.rstrip(), repr(line)

