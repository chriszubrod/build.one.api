"""Contract labor total_amount two-shot rounding (U-203).

Pins ``ContractLaborService.create`` / ``update_by_public_id`` and the shared
``labor_price_two_shot`` helper so contract-labor money matches T-SQL
``AggregateTimeEntryOnSubmit`` and the web two-shot policy. Pure-logic: patches
downstream services and uses a fake repo to capture kwargs — no live DB.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from entities.contract_labor.business.model import ContractLabor, ContractLaborLineItem
from entities.contract_labor.business.service import ContractLaborService
from shared.api.money import labor_price_two_shot, round_money

_CL_MODULE = "entities.contract_labor.business.service"

CANONICAL_HOURS = Decimal("0.75")
CANONICAL_RATE = Decimal("40.18")
CANONICAL_MARKUP = Decimal("0.25")
CANONICAL_TOTAL = Decimal("37.68")
SINGLE_SHOT_RAW = Decimal("37.66875")
SINGLE_SHOT_ONCE_ROUNDED = Decimal("37.67")
STALE_TOTAL = Decimal("1.00")

# U-200 parity fixture (tests/test_labor_price_two_shot_parity.py): pins ROUND_HALF_UP at both steps.
ROUND_MODE_HOURS = Decimal("4.49")
ROUND_MODE_RATE = Decimal("32.50")
ROUND_MODE_MARKUP = Decimal("0.50")
ROUND_MODE_HALF_UP_TOTAL = Decimal("218.90")
ROUND_MODE_HALF_EVEN_TOTAL = Decimal("218.88")


def _contract_labor(**overrides) -> ContractLabor:
    base = dict(
        id=1,
        public_id="p",
        row_version="cm==",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=1,
        project_id=None,
        employee_name="E",
        job_name=None,
        work_date="2026-08-02",
        time_in=None,
        time_out=None,
        break_time=None,
        regular_hours=None,
        overtime_hours=None,
        total_hours=CANONICAL_HOURS,
        hourly_rate=CANONICAL_RATE,
        markup=CANONICAL_MARKUP,
        total_amount=None,
        sub_cost_code_id=None,
        description="orig",
        billing_period_start="2026-08-15",
        status="pending_review",
        bill_line_item_id=None,
        bill_vendor_id=None,
        bill_date=None,
        due_date=None,
        bill_number=None,
        import_batch_id=None,
        source_file=None,
        source_row=None,
    )
    base.update(overrides)
    return ContractLabor(**base)


def _assert_canonical_total(value):
    assert isinstance(value, Decimal)
    assert value == CANONICAL_TOTAL
    assert value != SINGLE_SHOT_RAW
    assert value != SINGLE_SHOT_ONCE_ROUNDED
    assert value != SINGLE_SHOT_RAW
    assert value != SINGLE_SHOT_ONCE_ROUNDED


# --- labor_price_two_shot (direct) ---


def test_labor_price_two_shot_canonical_case():
    assert labor_price_two_shot(CANONICAL_HOURS, CANONICAL_RATE, CANONICAL_MARKUP) == CANONICAL_TOTAL


def test_labor_price_two_shot_markup_zero():
    assert labor_price_two_shot(CANONICAL_HOURS, CANONICAL_RATE, Decimal("0")) == round_money(
        CANONICAL_HOURS * CANONICAL_RATE
    )


def test_labor_price_two_shot_markup_none():
    assert labor_price_two_shot(CANONICAL_HOURS, CANONICAL_RATE, None) == round_money(
        CANONICAL_HOURS * CANONICAL_RATE
    )


def test_labor_price_two_shot_negative_hours_half_away_from_zero():
    assert labor_price_two_shot(Decimal("-0.75"), CANONICAL_RATE, CANONICAL_MARKUP) == Decimal("-37.68")


def test_round_money_half_up_not_half_even():
    assert round_money(Decimal("30.125")) == Decimal("30.13")
    assert round_money(Decimal("-30.125")) == Decimal("-30.13")


def test_labor_price_two_shot_round_mode_u200_parity_fixture():
    # 4.49 x 32.50 @ 50% — same inputs as U-200 parity in test_labor_price_two_shot_parity.py.
    result = labor_price_two_shot(ROUND_MODE_HOURS, ROUND_MODE_RATE, ROUND_MODE_MARKUP)
    assert result == ROUND_MODE_HALF_UP_TOTAL
    assert result != ROUND_MODE_HALF_EVEN_TOTAL


# --- model helpers ---


def test_contract_labor_calculate_total_amount():
    # Stored total_amount must not satisfy the assertion — calculator must recompute, not echo the field.
    cl = _contract_labor(total_amount=STALE_TOTAL)
    result = cl.calculate_total_amount()
    assert result != STALE_TOTAL
    _assert_canonical_total(result)


def test_contract_labor_line_item_calculate_price():
    hours = Decimal("6.75")
    rate = Decimal("230")
    markup = Decimal("0.05")
    expected = labor_price_two_shot(hours / Decimal("8"), rate, markup)
    assert expected == Decimal("203.76")
    item = ContractLaborLineItem(
        id=1,
        public_id="li",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        contract_labor_id=1,
        bill_line_item_id=None,
        line_date="2026-08-02",
        project_id=1,
        sub_cost_code_id=1,
        description="x",
        hours=hours,
        rate=rate,
        markup=markup,
        price=None,
        is_billable=True,
        is_overhead=False,
    )
    assert item.calculate_price() == expected


# --- ContractLaborService.create ---


@pytest.fixture(autouse=True)
def _patch_contract_labor_deps():
    with (
        patch(f"{_CL_MODULE}.VendorService") as vendor_cls,
        patch(f"{_CL_MODULE}.ProjectService"),
        patch(f"{_CL_MODULE}.SubCostCodeService"),
    ):
        vendor_cls.return_value.read_by_public_id.return_value = MagicMock(id=99)
        # Patch ProjectService/SubCostCodeService so future create tests with project/sub_cost args cannot hit a live DB.
        yield


def _run_create_capture(**create_kwargs):
    captured: dict = {}

    def _capture_create(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    fake_repo = MagicMock()
    fake_repo.create.side_effect = _capture_create
    service = ContractLaborService(repo=fake_repo)
    service.create(
        vendor_public_id="v",
        employee_name="E",
        work_date="2026-08-02",
        **create_kwargs,
    )
    return captured


def test_create_total_amount_two_shot():
    captured = _run_create_capture(
        total_hours=CANONICAL_HOURS,
        hourly_rate=CANONICAL_RATE,
        markup=CANONICAL_MARKUP,
    )
    _assert_canonical_total(captured["total_amount"])


def test_create_markup_none_no_markup_rounding():
    captured = _run_create_capture(
        total_hours=CANONICAL_HOURS,
        hourly_rate=CANONICAL_RATE,
        markup=None,
    )
    assert captured["total_amount"] == round_money(CANONICAL_HOURS * CANONICAL_RATE)
    assert captured["total_amount"] == Decimal("30.14")


def test_create_hourly_rate_none_leaves_total_amount_none():
    captured = _run_create_capture(
        total_hours=CANONICAL_HOURS,
        hourly_rate=None,
        markup=CANONICAL_MARKUP,
    )
    assert captured["total_amount"] is None


def test_create_total_amount_round_mode_u200_parity_fixture():
    # 4.49 x 32.50 @ 50% — same inputs as U-200 parity in test_labor_price_two_shot_parity.py.
    captured = _run_create_capture(
        total_hours=ROUND_MODE_HOURS,
        hourly_rate=ROUND_MODE_RATE,
        markup=ROUND_MODE_MARKUP,
    )
    assert captured["total_amount"] == ROUND_MODE_HALF_UP_TOTAL
    assert captured["total_amount"] != ROUND_MODE_HALF_EVEN_TOTAL


# --- ContractLaborService.update_by_public_id ---


def test_update_recalculates_total_amount_two_shot_on_touch():
    # Stale seed: same-value seed (CANONICAL_TOTAL) cannot distinguish recalc from never touched.
    existing = _contract_labor(total_amount=STALE_TOTAL)
    updated_row: dict = {}

    def _capture_update(row):
        updated_row["row"] = row
        return row

    fake_repo = MagicMock()
    fake_repo.read_by_public_id.return_value = existing
    fake_repo.update_by_id.side_effect = _capture_update
    service = ContractLaborService(repo=fake_repo)

    service.update_by_public_id(public_id="p", row_version="cm==", description="touched")

    assert updated_row["row"].total_amount != STALE_TOTAL
    _assert_canonical_total(updated_row["row"].total_amount)


def test_update_does_not_clobber_total_when_hourly_rate_none():
    # 999.99 is deliberately un-producible by two-shot recalc so preservation is meaningful.
    preserved = Decimal("999.99")
    existing = _contract_labor(hourly_rate=None, total_amount=preserved)
    updated_row: dict = {}

    def _capture_update(row):
        updated_row["row"] = row
        return row

    fake_repo = MagicMock()
    fake_repo.read_by_public_id.return_value = existing
    fake_repo.update_by_id.side_effect = _capture_update
    service = ContractLaborService(repo=fake_repo)

    service.update_by_public_id(public_id="p", row_version="cm==", description="touched")

    assert updated_row["row"].total_amount == preserved
