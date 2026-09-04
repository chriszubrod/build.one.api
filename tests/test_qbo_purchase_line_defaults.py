"""Pure-logic tests for U-098 — ExpenseLineItem qty/rate/markup defaults on QBO pull.

AccountBasedExpenseLineDetail (Ramp on 58999) has no Qty/UnitPrice; the connector
defaults 1×amount on CREATE and on UPDATE only when stored fields are NULL.
Re-sync must not clobber user-set qty/rate/markup.

These two pure decisions (`default_amount_only_line` / `preserve_stored_value`)
are asserted directly, independent of any connector wiring. The connector-wiring
coverage that used to live in this file (legacy mapping-table create/update/
fingerprint-adopt paths) is superseded by tests/test_u364_expense_line_item_
mapping_retire.py — U-364 retired the qbo.PurchaseLineExpenseLineItem mapping
table and repointed PurchaseLineExpenseLineItemConnector onto the shared
dbo-only line identity fast path, so those legacy-path tests no longer apply
(the fingerprint-adopt two-tier shim they exercised is retired too — see that
file's own readopt coverage, now single-tier via base/line_orphan_adopt.py).
"""
from decimal import Decimal

import pytest

from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    default_amount_only_line,
    preserve_stored_value,
)


@pytest.mark.parametrize(
    "qty, unit_price, amount, expected",
    [
        # Amount-only (Ramp AccountBasedExpenseLineDetail) -> 1 x amount.
        (None, None, Decimal("300"), (Decimal("1"), Decimal("300"))),
        # Either field present -> untouched, no defaulting.
        (Decimal("2.5"), Decimal("100"), Decimal("250"), (Decimal("2.5"), Decimal("100"))),
        (Decimal("2"), None, Decimal("250"), (Decimal("2"), None)),
        (None, Decimal("100"), Decimal("250"), (None, Decimal("100"))),
        # Explicit zeros are real values, not missing ones.
        (Decimal("0"), Decimal("0"), Decimal("300"), (Decimal("0"), Decimal("0"))),
        # No amount to derive a rate from -> untouched.
        (None, None, None, (None, None)),
    ],
)
def test_default_amount_only_line(qty, unit_price, amount, expected):
    assert default_amount_only_line(qty, unit_price, amount) == expected


@pytest.mark.parametrize(
    "default_value, qbo_value, stored_value, expected",
    [
        # QBO omitted it and the user already set one -> None = "leave it alone".
        (Decimal("1"), None, Decimal("3"), None),
        # QBO omitted it and there is nothing stored -> fill the hole.
        (Decimal("1"), None, None, Decimal("1")),
        # QBO supplied it -> QBO wins, stored value or not.
        (Decimal("2.5"), Decimal("2.5"), Decimal("3"), Decimal("2.5")),
        (Decimal("2.5"), Decimal("2.5"), None, Decimal("2.5")),
        # A stored zero is a value worth preserving.
        (Decimal("1"), None, Decimal("0"), None),
    ],
)
def test_preserve_stored_value(default_value, qbo_value, stored_value, expected):
    assert preserve_stored_value(default_value, qbo_value, stored_value) == expected
