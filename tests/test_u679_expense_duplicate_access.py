"""U-679 — expense duplicate handling must not leak inaccessible expenses (P0)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.business.service import ExpenseService
from shared.api.errors import ApiError
from shared.api.responses import raise_workflow_error
from shared.authz import clear_authz_context, set_authz_context


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _existing_row(**over):
    base = dict(id=55, public_id="exp-55-secret", reference_number="REF-1", vendor_id=7)
    base.update(over)
    return SimpleNamespace(**base)


def _svc(existing):
    repo = MagicMock()
    repo.read_by_reference_number_and_vendor_id.return_value = existing
    repo.create.return_value = SimpleNamespace(public_id="new-exp", id=99)
    svc = ExpenseService(repo=repo)
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_public_id.return_value = None
    return svc, repo


def _try_create(svc, *, attachment_public_id=None):
    with patch("entities.expense.business.service.VendorService") as mock_vendor, patch(
        "entities.email_message.persistence.repo.EmailAttachmentRepository"
    ) as mock_ea_repo, patch(
        "entities.email_message.business.service.EmailAttachmentBridgeService"
    ) as mock_bridge:
        mock_vendor.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        mock_ea_repo.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
        mock_bridge.return_value.bridge.return_value = SimpleNamespace(
            public_id="bridged-att",
            id=2,
            content_type="application/pdf",
        )
        with pytest.raises(ValueError) as exc:
            svc.create(
                vendor_public_id="vendor-1",
                expense_date="2026-09-01",
                reference_number="REF-1",
                attachment_public_id=attachment_public_id,
            )
        return exc.value, mock_bridge


def test_accessible_duplicate_message_unchanged():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _ = _svc(_existing_row())
    with patch(
        "entities.expense.business.service.can_access_expense",
        return_value=True,
    ) as mock_access:
        err, _bridge = _try_create(svc)
        mock_access.assert_called_once_with(55)
    assert str(err) == (
        "An expense with ReferenceNumber 'REF-1' already exists for this vendor. "
        "Please update the existing expense instead of creating a new one."
    )


def test_inaccessible_duplicate_message_is_generic():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _ = _svc(_existing_row())
    with patch(
        "entities.expense.business.service.can_access_expense",
        return_value=False,
    ):
        err, _ = _try_create(svc)
    msg = str(err)
    assert msg == "An expense with this reference number already exists for this vendor."
    assert "ReferenceNumber" not in msg
    assert "update the existing" not in msg.lower()


def test_inaccessible_duplicate_with_email_attachment_does_not_bridge():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _ = _svc(_existing_row())
    with patch(
        "entities.expense.business.service.can_access_expense",
        return_value=False,
    ):
        _err, mock_bridge = _try_create(svc, attachment_public_id="ea-pub-1")
    mock_bridge.return_value.bridge.assert_not_called()


def test_duplicate_access_check_db_error_uses_generic_message():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _ = _svc(_existing_row())
    driver_detail = "ODBC driver timeout secret-xyz"
    with patch("shared.access._check", side_effect=RuntimeError(driver_detail)):
        err, _ = _try_create(svc, attachment_public_id="ea-pub-1")
    msg = str(err)
    assert driver_detail not in msg
    assert msg == "An expense with this reference number already exists for this vendor."


def test_non_duplicate_still_bridges_email_attachment():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.read_by_reference_number_and_vendor_id.return_value = None
    repo.create.return_value = SimpleNamespace(public_id="new-exp", id=99)
    svc = ExpenseService(repo=repo)
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_public_id.return_value = None
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.create.return_value = SimpleNamespace(
        public_id="line-1", id=1
    )
    svc._expense_line_item_attachment_service = MagicMock()
    with patch("entities.expense.business.service.VendorService") as mock_vendor, patch(
        "entities.email_message.persistence.repo.EmailAttachmentRepository"
    ) as mock_ea_repo, patch(
        "entities.email_message.business.service.EmailAttachmentBridgeService"
    ) as mock_bridge:
        mock_vendor.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        mock_ea_repo.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
        mock_bridge.return_value.bridge.return_value = SimpleNamespace(
            public_id="bridged-att",
            id=2,
            content_type="application/pdf",
        )
        svc.create(
            vendor_public_id="vendor-1",
            expense_date="2026-09-01",
            reference_number="REF-NEW",
            attachment_public_id="ea-pub-1",
        )
        mock_bridge.return_value.bridge.assert_called_once_with(
            email_attachment_public_id="ea-pub-1",
        )


def test_raise_workflow_error_maps_generic_duplicate_to_409():
    msg = "An expense with this reference number already exists for this vendor."
    with pytest.raises(ApiError) as exc:
        raise_workflow_error(msg, "Failed to create expense")
    assert exc.value.status_code == 409
