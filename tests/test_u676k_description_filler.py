"""U-676k: short description filler-word backstop (table-driven)."""
from __future__ import annotations

import pytest

from entities.bill.business.description_format import format_short_description


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Poplar S4S Lumber Purchase", "Poplar S4S Lumber"),
        ("Purchase", "Purchase"),
        ("Delivery Fee", "Delivery Fee"),
        ("poplar s4s lumber PURCHASE", "Poplar S4s Lumber"),
        ("Change Order", "Change Order"),
        ("Electric Bill", "Electric Bill"),
        ("Finance Charge", "Finance Charge"),
        ("Down Payment", "Down Payment"),
        ("Purchase Order", "Purchase Order"),
        ("Water Bill", "Water Bill"),
        ("Late Charge", "Late Charge"),
        ("Progress Payment", "Progress Payment"),
        ("Poplar S4S Lumber Purchases", "Poplar S4S Lumber"),
    ],
    ids=[
        "lumber_purchase",
        "one_word_purchase_kept",
        "delivery_fee_unchanged",
        "case_insensitive",
        "change_order_unchanged",
        "electric_bill_unchanged",
        "finance_charge_unchanged",
        "down_payment_unchanged",
        "purchase_order_unchanged",
        "water_bill_unchanged",
        "late_charge_unchanged",
        "progress_payment_unchanged",
        "purchases_plural",
    ],
)
def test_trailing_filler_stripped(raw, expected):
    assert format_short_description(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "Kitchen Cabinet Hardware Purchase Order Details Extra Words Here",
            "Kitchen Cabinet Hardware Purchase Order",
        ),
        (
            "Alpha Beta Gamma Delta Epsilon Zeta Purchase",
            "Alpha Beta Gamma Delta Epsilon",
        ),
    ],
    ids=["post_cut_purchase_tail", "post_cut_purchase_after_five_word_cap"],
)
def test_filler_strip_after_sixty_char_cut(raw, expected):
    assert format_short_description(raw) == expected


def test_filler_strip_idempotent():
    cases = [
        "Poplar S4S Lumber Purchase",
        "Kitchen Cabinet Hardware Purchase Order Details Extra Words Here",
        "Purchase Order",
        "Change Order",
    ]
    for raw in cases:
        once = format_short_description(raw)
        twice = format_short_description(once)
        assert once == twice, raw
