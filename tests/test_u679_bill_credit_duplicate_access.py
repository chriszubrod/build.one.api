"""U-679 — bill credit duplicate handling must not leak inaccessible bill credits (P0)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill_credit.business.service import BillCreditService
from shared.api.errors import ApiError
from shared.api.responses import raise_workflow_error
from shared.authz import clear_authz_context, set_authz_context


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _existing(**over):
    base = dict(id=55, public_id="bc-55-secret", credit_number="CR-1", vendor_id=7)
    base.update(over)
    return SimpleNamespace(**base)


def _svc(existing):
    repo = MagicMock()
    repo.read_by_credit_number_and_vendor_id.return_value = existing
    return BillCreditService(repo=repo), repo


def _try_create(svc):
    with patch("entities.bill_credit.business.service.VendorService") as mock_vendor:
        mock_vendor.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        with pytest.raises(ValueError) as exc:
            svc.create(
                vendor_public_id="vendor-1",
                credit_date="2026-09-01",
                credit_number="CR-1",
            )
        return exc.value


def test_accessible_duplicate_message_unchanged():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _ = _svc(_existing())
    with patch(
        "entities.bill_credit.business.service.can_access_bill_credit",
        return_value=True,
    ) as mock_access:
        err = _try_create(svc)
        mock_access.assert_called_once_with(55)
    assert str(err) == (
        "A bill credit with CreditNumber 'CR-1' already exists for this vendor. "
        "Please update the existing bill credit instead of creating a new one."
    )


def test_inaccessible_duplicate_message_is_generic():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _ = _svc(_existing())
    with patch(
        "entities.bill_credit.business.service.can_access_bill_credit",
        return_value=False,
    ):
        err = _try_create(svc)
    msg = str(err)
    assert msg == "A bill credit with this number already exists for this vendor."
    assert "CreditNumber" not in msg
    assert "update the existing" not in msg.lower()


def test_duplicate_access_check_db_error_uses_generic_message():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _ = _svc(_existing())
    driver_detail = "ODBC driver timeout secret-xyz"
    with patch("shared.access._check", side_effect=RuntimeError(driver_detail)):
        err = _try_create(svc)
    msg = str(err)
    assert driver_detail not in msg
    assert msg == "A bill credit with this number already exists for this vendor."


def test_raise_workflow_error_maps_generic_duplicate_to_409():
    msg = "A bill credit with this number already exists for this vendor."
    with pytest.raises(ApiError) as exc:
        raise_workflow_error(msg, "Failed to create bill credit")
    assert exc.value.status_code == 409
