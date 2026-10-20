"""shared.access.can_access_* — fail-closed boolean gates (U-678 / U-679)."""

from __future__ import annotations

from typing import Callable

from unittest.mock import patch

import pytest

from shared.access import can_access_bill, can_access_bill_credit, can_access_expense
from shared.authz import clear_authz_context, set_authz_context

ACCESS_CASES = (
    pytest.param(can_access_bill, "UserCanAccessBill", "bill", id="bill"),
    pytest.param(can_access_bill_credit, "UserCanAccessBillCredit", "bill_credit", id="bill_credit"),
    pytest.param(can_access_expense, "UserCanAccessExpense", "expense", id="expense"),
)


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


@pytest.mark.parametrize("fn,udf,label", ACCESS_CASES)
def test_can_access_bypass_when_system_admin(
    fn: Callable[[int | None], bool], udf: str, label: str
):
    set_authz_context(user_id=20, company_id=1, is_system_admin=True)
    with patch("shared.access._check") as mock_check:
        assert fn(55) is True
    mock_check.assert_not_called()


@pytest.mark.parametrize("fn,udf,label", ACCESS_CASES)
def test_can_access_denies_when_entity_id_none(
    fn: Callable[[int | None], bool], udf: str, label: str
):
    """No entity id → no access, even for a system admin (a boolean must not grant on None)."""
    for admin in (False, True):
        set_authz_context(user_id=20, company_id=1, is_system_admin=admin)
        with patch("shared.access._check") as mock_check:
            assert fn(None) is False
        mock_check.assert_not_called()


@pytest.mark.parametrize("fn,udf,label", ACCESS_CASES)
def test_can_access_true_when_udf_allows(
    fn: Callable[[int | None], bool], udf: str, label: str
):
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=True) as mock_check:
        assert fn(55) is True
    mock_check.assert_called_once_with(udf, 55)


@pytest.mark.parametrize("fn,udf,label", ACCESS_CASES)
def test_can_access_false_when_udf_denies(
    fn: Callable[[int | None], bool], udf: str, label: str
):
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=False):
        assert fn(55) is False


@pytest.mark.parametrize("fn,udf,label", ACCESS_CASES)
def test_can_access_db_error_fail_closed_no_exception_text_in_log(
    fn: Callable[[int | None], bool],
    udf: str,
    label: str,
    caplog,
):
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    secret = "pyodbc-secret-driver-message"
    with patch(
        "shared.access._check",
        side_effect=RuntimeError(secret),
    ):
        with caplog.at_level("WARNING"):
            assert fn(55) is False
    logged = caplog.text
    assert secret not in logged
    assert "RuntimeError" in logged
