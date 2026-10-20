"""shared.access.can_access_bill — fail-closed boolean gate (U-678)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shared.access import can_access_bill
from shared.authz import clear_authz_context, set_authz_context


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def test_can_access_bill_bypass_when_system_admin():
    set_authz_context(user_id=20, company_id=1, is_system_admin=True)
    with patch("shared.access._check") as mock_check:
        assert can_access_bill(55) is True
    mock_check.assert_not_called()


def test_can_access_bill_denies_when_bill_id_none():
    """No bill id → no access, even for a system admin (a boolean must not grant on None)."""
    for admin in (False, True):
        set_authz_context(user_id=20, company_id=1, is_system_admin=admin)
        with patch("shared.access._check") as mock_check:
            assert can_access_bill(None) is False
        mock_check.assert_not_called()


def test_can_access_bill_true_when_udf_allows():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=True) as mock_check:
        assert can_access_bill(55) is True
    mock_check.assert_called_once_with("UserCanAccessBill", 55)


def test_can_access_bill_false_when_udf_denies():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=False):
        assert can_access_bill(55) is False


def test_can_access_bill_db_error_fail_closed_no_exception_text_in_log(caplog):
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    secret = "pyodbc-secret-driver-message"
    with patch(
        "shared.access._check",
        side_effect=RuntimeError(secret),
    ):
        with caplog.at_level("WARNING"):
            assert can_access_bill(55) is False
    logged = caplog.text
    assert secret not in logged
    assert "RuntimeError" in logged
