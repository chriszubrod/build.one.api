"""U-676k: short description filler-word backstop (table-driven)."""
from __future__ import annotations

import pytest

from entities.bill.business.description_format import format_short_description


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Poplar S4S Lumber Purchase", "Poplar S4S Lumber"),
        ("Lumber purchase order", "Lumber"),
        ("Purchase", "Purchase"),
        ("Delivery Fee", "Delivery Fee"),
        ("poplar s4s lumber PURCHASE", "Poplar S4s Lumber"),
        ("Materials charge", "Materials"),
    ],
    ids=[
        "lumber_purchase",
        "purchase_order_trailing",
        "one_word_purchase_kept",
        "delivery_fee_unchanged",
        "case_insensitive",
        "materials_not_filler",
    ],
)
def test_trailing_filler_stripped(raw, expected):
    assert format_short_description(raw) == expected


def test_filler_strip_idempotent():
    once = format_short_description("Poplar S4S Lumber Purchase")
    twice = format_short_description(once)
    assert once == twice == "Poplar S4S Lumber"
