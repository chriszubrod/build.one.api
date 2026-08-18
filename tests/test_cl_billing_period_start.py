"""Contract labor billing period START (U-266).

Pins ``ContractLabor.calculate_billing_period_start`` to return the period
START (1st / 16th), matching existing ``dbo.ContractLabor.BillingPeriodStart``
rows and the exact-match filter in ``ReadContractLaborsByStatus``. The method
previously returned the period END (15th / last-day-of-month), which stamped
rows created via ``ContractLaborService.create`` invisibly to
``generate_bills_for_vendor``. Pure-logic, no DB.

Also pins ``ContractLaborBillService._period_end_for`` (promoted from a local
closure to a shared method as part of this fix, since two call sites used to
read ``entry.billing_period_start`` for period-END purposes and must now
derive it from ``work_date`` instead) — ``billing_period_start`` and the
period END are independent values computed from the same date, never each
other.
"""

import pytest

from entities.contract_labor.business.bill_service import ContractLaborBillService
from entities.contract_labor.business.model import ContractLabor


@pytest.mark.parametrize(
    "work_date, expected",
    [
        ("2026-06-05", "2026-06-01"),
        ("2026-06-15", "2026-06-01"),
        ("2026-06-16", "2026-06-16"),
        ("2026-06-20", "2026-06-16"),
        ("2026-02-28", "2026-02-16"),
    ],
)
def test_calculate_billing_period_start(work_date, expected):
    assert ContractLabor.calculate_billing_period_start(work_date) == expected


def test_calculate_billing_period_start_none_input():
    assert ContractLabor.calculate_billing_period_start(None) is None
    assert ContractLabor.calculate_billing_period_start("") is None


def test_calculate_billing_period_start_malformed_input():
    assert ContractLabor.calculate_billing_period_start("not-a-date") is None


@pytest.mark.parametrize(
    "work_date, expected_end",
    [
        ("2026-06-05", "2026-06-15"),
        ("2026-06-15", "2026-06-15"),
        ("2026-06-16", "2026-06-30"),
        ("2026-06-20", "2026-06-30"),
        ("2026-02-28", "2026-02-28"),
    ],
)
def test_period_end_for_independent_of_billing_period_start(work_date, expected_end):
    """The period END derived from work_date must never equal the period
    START stamped for the same date — proves the two fallback sites this
    unit repointed (generate_bills_for_vendor, _generate_combined_pdf) can no
    longer silently collapse to reading entry.billing_period_start."""
    svc = ContractLaborBillService()
    period_start = ContractLabor.calculate_billing_period_start(work_date)
    period_end = svc._period_end_for(work_date)

    assert period_end == expected_end
    assert period_end != period_start


def test_period_end_for_none_and_malformed_input():
    svc = ContractLaborBillService()
    assert svc._period_end_for(None) is None
    assert svc._period_end_for("") is None
    assert svc._period_end_for(str(None)) is None
    assert svc._period_end_for("not-a-date") is None
