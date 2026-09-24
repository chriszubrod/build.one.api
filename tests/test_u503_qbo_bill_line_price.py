"""U-503: QBO bill line customer price from extended Amount + markup (pure logic)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import (
    BillLineItemConnector,
    compute_qbo_bill_line_customer_price,
)
from shared.api.money import round_money




def test_price_from_amount_and_markup_real_bli_23861():
    # Old code: unit_price * (1 + markup%) without qty — 34.69 for this row.
    price = compute_qbo_bill_line_customer_price(
        amount=Decimal("23.13"),
        qty=Decimal("1"),
        rate=Decimal("23.13"),
        markup_percent=Decimal("50.01"),
    )
    assert price == Decimal("34.70")
    assert type(price) is Decimal


def test_price_from_amount_qty_not_one_real_bli_2501():
    # Old code used unit rate only → 52.50; extended amount 183.75 @ 50% → 275.63.
    price = compute_qbo_bill_line_customer_price(
        amount=Decimal("183.75"),
        qty=Decimal("5.25"),
        rate=Decimal("35"),
        markup_percent=Decimal("50"),
    )
    assert price == Decimal("275.63")
    assert type(price) is Decimal


def test_two_shot_boundary_uses_amount_not_qty_times_rate():
    """Amount basis vs single-shot on qty×rate: 37.68 vs 37.67 (both verified)."""
    amount = Decimal("30.14")
    qty = Decimal(".75")
    rate = Decimal("40.18")
    markup_percent = Decimal("25")
    expected = Decimal("37.68")
    single_shot_on_rate = round_money(qty * rate * (Decimal("1") + markup_percent / Decimal("100")))

    price = compute_qbo_bill_line_customer_price(
        amount=amount,
        qty=qty,
        rate=rate,
        markup_percent=markup_percent,
    )
    assert price == expected
    assert price != Decimal("37.67")
    assert single_shot_on_rate == Decimal("37.67")
    assert type(price) is Decimal


def test_negative_amount_preserves_sign_with_markup():
    # rate deliberately != amount: the OLD rate-based formula would give -60.00 here,
    # so this fixture discriminates instead of coincidentally agreeing.
    price = compute_qbo_bill_line_customer_price(
        amount=Decimal("-100.00"),
        qty=Decimal("2.5"),
        rate=Decimal("-40.00"),
        markup_percent=Decimal("50"),
    )
    assert price == Decimal("-150.00")
    assert price != Decimal("-60.00")
    assert price < Decimal("0")
    assert type(price) is Decimal


def test_zero_markup_price_equals_amount():
    amount = Decimal("100.00")
    price = compute_qbo_bill_line_customer_price(
        amount=amount,
        qty=Decimal("2"),
        rate=Decimal("50"),
        markup_percent=Decimal("0"),
    )
    assert price == amount
    assert type(price) is Decimal


def test_no_markup_percent_price_is_none():
    price = compute_qbo_bill_line_customer_price(
        amount=Decimal("99.99"),
        qty=Decimal("1"),
        rate=Decimal("99.99"),
        markup_percent=None,
    )
    assert price is None


def test_amount_none_falls_back_to_labor_price_two_shot():
    price = compute_qbo_bill_line_customer_price(
        amount=None,
        qty=Decimal("5"),
        rate=Decimal("35"),
        markup_percent=Decimal("50"),
    )
    # cost = round(5 * 35) = 175; price = round(175 * 1.5) = 262.50
    assert price == Decimal("262.50")
    assert type(price) is Decimal




def _make_qbo_bill_line(**overrides):
    defaults = dict(
        id=42,
        qbo_bill_id=4,
        qbo_line_id="1",
        description="Line",
        amount=Decimal("100.00"),
        qty=Decimal("1"),
        unit_price=Decimal("100.00"),
        markup_percent=Decimal("25"),
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector_for_price_wiring():
    connector = BillLineItemConnector()
    bill_svc = Mock()
    bill_svc.read_by_id.return_value = SimpleNamespace(id=19146, public_id="bill-pub")
    line_svc = Mock()
    line_svc.read_by_bill_id.return_value = []
    connector.bill_service = bill_svc
    connector.bill_line_item_service = line_svc
    connector.reconciliation_repo = Mock()
    connector._get_project_public_id = Mock(return_value=None)
    return connector, line_svc


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_sync_from_qbo_bill_line_hit_passes_computed_price_to_update():
    amount = Decimal("30.14")
    markup_percent = Decimal("25")

    connector, line_svc = _build_connector_for_price_wiring()
    qbo_line = _make_qbo_bill_line(
        amount=amount,
        qty=Decimal(".75"),
        unit_price=Decimal("40.18"),
        markup_percent=markup_percent,
    )
    direct = SimpleNamespace(
        id=55,
        public_id="pub-55",
        row_version="rv-55",
        qbo_id="1",
        realm_id="realm-1",
    )
    line_svc.read_by_qbo_identity.return_value = direct
    updated = SimpleNamespace(id=55, public_id="pub-55")
    line_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_bill_line(
        19146, qbo_line, frozenset({"1"}), realm_id="realm-1"
    )

    assert result is updated
    line_svc.update_by_public_id.assert_called_once()
    assert line_svc.update_by_public_id.call_args.kwargs["price"] == Decimal("37.68")
    line_svc.create.assert_not_called()


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_sync_from_qbo_bill_line_miss_passes_computed_price_to_create():
    amount = Decimal("-100.00")
    markup_percent = Decimal("50")

    connector, line_svc = _build_connector_for_price_wiring()
    qbo_line = _make_qbo_bill_line(
        amount=amount,
        qty=Decimal("2.5"),
        unit_price=Decimal("-40.00"),
        markup_percent=markup_percent,
    )
    line_svc.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.create.return_value = created
    line_svc.read_by_id.return_value = SimpleNamespace(
        id=77,
        public_id="pub-77",
        row_version="rv-77",
        qbo_id="1",
        realm_id="realm-1",
    )

    connector.sync_from_qbo_bill_line(
        19146, qbo_line, frozenset({"1"}), realm_id="realm-1"
    )

    line_svc.create.assert_called_once()
    assert line_svc.create.call_args.kwargs["price"] == Decimal("-150.00")
    line_svc.update_by_public_id.assert_not_called()
