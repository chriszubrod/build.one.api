"""Pure-logic tests for U-098 — ExpenseLineItem qty/rate/markup defaults on QBO pull.

AccountBasedExpenseLineDetail (Ramp on 58999) has no Qty/UnitPrice; the connector
defaults 1×amount on CREATE and on UPDATE only when stored fields are NULL.
Re-sync must not clobber user-set qty/rate/markup. Fingerprint adoption uses a
two-tier match (raw exact, then normalized fallback) so legacy NULL rows still match
without redirecting pre-patch exact adoptions.

Mocks only — no DB or QBO I/O.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
    default_amount_only_line,
    preserve_stored_value,
)
from integrations.intuit.qbo.purchase.business.model import QboPurchaseLine


# --------------------------------------------------------------------------- #
# The two pure decisions, asserted directly. These pin the contract independent
# of the connector wiring below, so lifting either helper into base/ (see the
# comment at its definition) can't silently change what it decides.
# --------------------------------------------------------------------------- #

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


def _make_qbo_line(
    *,
    line_id=101,
    description="Materials",
    amount=Decimal("300"),
    qty=None,
    unit_price=None,
    markup_percent=None,
):
    return QboPurchaseLine(
        id=line_id,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_purchase_id=1,
        qbo_line_id="1",
        line_num=1,
        description=description,
        amount=amount,
        detail_type="AccountBasedExpenseLineDetail",
        item_ref_value=None,
        item_ref_name=None,
        account_ref_value="58999",
        account_ref_name=None,
        customer_ref_value=None,
        customer_ref_name=None,
        class_ref_value=None,
        class_ref_name=None,
        billable_status=None,
        qty=qty,
        unit_price=unit_price,
        markup_percent=markup_percent,
    )


def _make_line_item(
    *,
    line_item_id=50,
    public_id="eli-pub-50",
    row_version="rv==",
    quantity=None,
    rate=None,
    markup=None,
    description="Materials",
    amount=Decimal("300"),
):
    return SimpleNamespace(
        id=line_item_id,
        public_id=public_id,
        row_version=row_version,
        quantity=quantity,
        rate=rate,
        markup=markup,
        description=description,
        amount=amount,
    )


def _build_connector():
    mapping_repo = Mock()
    mapping_repo.read_by_qbo_purchase_line_id.return_value = None
    mapping_repo.read_by_expense_line_item_id.return_value = None
    expense_line_item_service = Mock()
    expense_line_item_service.read_by_expense_id.return_value = []
    connector = PurchaseLineExpenseLineItemConnector(
        mapping_repo=mapping_repo,
        expense_line_item_service=expense_line_item_service,
        item_sub_cost_code_repo=Mock(),
        qbo_item_repo=Mock(),
        customer_project_repo=Mock(),
        qbo_customer_repo=Mock(),
    )
    return connector, mapping_repo, expense_line_item_service


def test_amount_only_create_defaults_qty_rate_markup():
    connector, mapping_repo, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(amount=Decimal("300"))
    created = _make_line_item(quantity=Decimal("1"), rate=Decimal("300"), markup=Decimal("0"))
    eli_svc.create.return_value = created
    mapping_repo.create.return_value = SimpleNamespace(id=1, expense_line_item_id=50)

    connector.sync_from_qbo_purchase_line(
        expense_id=10,
        expense_public_id="exp-pub",
        qbo_line=qbo_line,
    )

    eli_svc.create.assert_called_once()
    kw = eli_svc.create.call_args.kwargs
    assert kw["quantity"] == Decimal("1")
    assert kw["rate"] == Decimal("300")
    assert kw["markup"] == Decimal("0")
    assert kw["price"] == Decimal("300")


def test_has_qty_create_passes_through():
    connector, _, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(
        qty=Decimal("2.5"),
        unit_price=Decimal("100"),
        amount=Decimal("250"),
    )
    eli_svc.create.return_value = _make_line_item(
        quantity=Decimal("2.5"), rate=Decimal("100")
    )
    connector.mapping_repo.create.return_value = SimpleNamespace(
        id=1, expense_line_item_id=50
    )

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line)

    kw = eli_svc.create.call_args.kwargs
    assert kw["quantity"] == Decimal("2.5")
    assert kw["rate"] == Decimal("100")
    assert kw["amount"] == Decimal("250")


def test_explicit_zeros_not_replaced_by_amount_only_default():
    connector, _, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(
        qty=Decimal("0"),
        unit_price=Decimal("0"),
        amount=Decimal("300"),
    )
    eli_svc.create.return_value = _make_line_item(quantity=Decimal("0"), rate=Decimal("0"))
    connector.mapping_repo.create.return_value = SimpleNamespace(
        id=1, expense_line_item_id=50
    )

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line)

    kw = eli_svc.create.call_args.kwargs
    assert kw["quantity"] == Decimal("0")
    assert kw["rate"] == Decimal("0")


def test_dont_clobber_on_resync_user_set_qty_rate_markup():
    connector, mapping_repo, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(amount=Decimal("300"))
    line_item = _make_line_item(
        quantity=Decimal("3"),
        rate=Decimal("100"),
        markup=Decimal("0.10"),
    )
    mapping = SimpleNamespace(id=1, expense_line_item_id=50)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = mapping
    eli_svc.read_by_id.return_value = line_item
    eli_svc.update_by_public_id.return_value = line_item

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line)

    eli_svc.create.assert_not_called()
    kw = eli_svc.update_by_public_id.call_args.kwargs
    assert kw["quantity"] is None
    assert kw["rate"] is None
    assert kw["markup"] is None


def test_heal_legacy_null_on_resync():
    connector, mapping_repo, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(amount=Decimal("300"))
    line_item = _make_line_item(quantity=None, rate=None, markup=None)
    mapping = SimpleNamespace(id=1, expense_line_item_id=50)
    mapping_repo.read_by_qbo_purchase_line_id.return_value = mapping
    eli_svc.read_by_id.return_value = line_item
    eli_svc.update_by_public_id.return_value = line_item

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line)

    kw = eli_svc.update_by_public_id.call_args.kwargs
    assert kw["quantity"] == Decimal("1")
    assert kw["rate"] == Decimal("300")
    assert kw["markup"] == Decimal("0")


def test_fingerprint_adopt_survives_amount_only_normalization():
    connector, mapping_repo, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(
        line_id=999,
        description="Ramp spend",
        amount=Decimal("300"),
    )
    orphan = _make_line_item(
        line_item_id=77,
        description="Ramp spend",
        amount=Decimal("300"),
        quantity=None,
        rate=None,
    )
    eli_svc.read_by_expense_id.return_value = [orphan]
    mapping_repo.read_by_expense_line_item_id.return_value = None
    adopted_mapping = SimpleNamespace(id=2, expense_line_item_id=77)
    mapping_repo.create.return_value = adopted_mapping
    eli_svc.read_by_id.return_value = orphan
    eli_svc.update_by_public_id.return_value = orphan

    result = connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line)

    eli_svc.create.assert_not_called()
    mapping_repo.create.assert_called_once_with(
        expense_line_item_id=77,
        qbo_purchase_line_id=999,
    )
    assert result is orphan


def test_fingerprint_tier1_wins_over_normalized_candidate_no_redirect():
    """Amount-only QBO must adopt the raw-exact legacy row, not a (1×amount) sibling."""
    connector, mapping_repo, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(
        line_id=999,
        description="Ramp spend",
        amount=Decimal("300"),
    )
    explicit = _make_line_item(
        line_item_id=50,
        public_id="eli-explicit",
        description="Ramp spend",
        amount=Decimal("300"),
        quantity=Decimal("1"),
        rate=Decimal("300"),
    )
    legacy = _make_line_item(
        line_item_id=77,
        public_id="eli-legacy",
        description="Ramp spend",
        amount=Decimal("300"),
        quantity=None,
        rate=None,
    )
    eli_svc.read_by_expense_id.return_value = [explicit, legacy]
    mapping_repo.read_by_expense_line_item_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(id=2, expense_line_item_id=77)
    eli_svc.read_by_id.return_value = legacy
    eli_svc.update_by_public_id.return_value = legacy

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line)

    eli_svc.create.assert_not_called()
    mapping_repo.create.assert_called_once_with(
        expense_line_item_id=77,
        qbo_purchase_line_id=999,
    )


def test_fingerprint_tier2_adopts_post_fix_stored_shape():
    """When no raw-exact candidate exists, normalized fallback adopts (qty=1, rate=amount)."""
    connector, mapping_repo, eli_svc = _build_connector()
    qbo_line = _make_qbo_line(
        line_id=999,
        description="Ramp spend",
        amount=Decimal("300"),
    )
    stored = _make_line_item(
        line_item_id=88,
        description="Ramp spend",
        amount=Decimal("300"),
        quantity=Decimal("1"),
        rate=Decimal("300"),
    )
    eli_svc.read_by_expense_id.return_value = [stored]
    mapping_repo.read_by_expense_line_item_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(id=2, expense_line_item_id=88)
    eli_svc.read_by_id.return_value = stored
    eli_svc.update_by_public_id.return_value = stored

    connector.sync_from_qbo_purchase_line(10, "exp-pub", qbo_line)

    eli_svc.create.assert_not_called()
    mapping_repo.create.assert_called_once_with(
        expense_line_item_id=88,
        qbo_purchase_line_id=999,
    )
