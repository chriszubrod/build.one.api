"""U-678 — bill duplicate handling must not leak inaccessible bills (P0)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill.business.service import BillService, DuplicateBillError
from shared.api.errors import ApiError
from shared.api.responses import raise_workflow_error
from shared.authz import clear_authz_context, set_authz_context, system_authz


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _existing_bill(**over):
    base = dict(
        id=55,
        public_id="bill-55-secret",
        is_draft=True,
        status="in_review",
        vendor_id=7,
        bill_number="INV-1",
        bill_date="2026-09-01",
        total_amount=None,
        source_email_message_id=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _bill_service(existing):
    repo = MagicMock()
    repo.read_by_bill_number_and_vendor_id.return_value = existing
    return BillService(repo=repo), repo


def _try_create(svc, *, source_email_message_public_id=None):
    with patch("entities.bill.business.service.VendorService") as mock_vendor, patch(
        "entities.email_message.business.service.EmailMessageService"
    ) as mock_email:
        mock_vendor.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        if source_email_message_public_id is not None:
            mock_email.return_value.read_by_public_id.return_value = SimpleNamespace(id=123)
        try:
            svc.create(
                vendor_public_id="vendor-1",
                bill_number="INV-1",
                bill_date="2026-09-01",
                due_date="2026-09-01",
                is_draft=True,
                require_attachment=False,
                source_email_message_public_id=source_email_message_public_id,
            )
        except DuplicateBillError as err:
            return err
    pytest.fail("expected DuplicateBillError")


def test_accessible_duplicate_message_still_names_the_bill():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _repo = _bill_service(_existing_bill(public_id="bill-accessible"))
    with patch(
        "entities.bill.business.service.can_access_bill",
        return_value=True,
    ) as mock_access:
        err = _try_create(svc)
        mock_access.assert_called_once_with(55)
    assert "Bill.PublicId=bill-accessible" in str(err)
    assert "IsDraft=" in str(err)
    assert "already exists" in str(err).lower()
    assert err.existing_bill_public_id == "bill-accessible"


def test_duplicate_access_check_db_error_uses_generic_message_no_driver_text():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _repo = _bill_service(_existing_bill())
    driver_detail = "ODBC driver timeout secret-xyz"
    with patch("shared.access._check", side_effect=RuntimeError(driver_detail)):
        err = _try_create(svc)
    msg = str(err)
    assert driver_detail not in msg
    assert "Bill.PublicId" not in msg
    assert "already exists" in msg.lower()
    assert err.existing_bill_public_id == "bill-55-secret"


def test_inaccessible_duplicate_message_is_generic_and_keeps_attribute():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, _repo = _bill_service(_existing_bill())
    with patch(
        "entities.bill.business.service.can_access_bill",
        return_value=False,
    ):
        err = _try_create(svc)
    msg = str(err)
    assert "already exists" in msg.lower()
    assert "Bill.PublicId" not in msg
    assert "IsDraft" not in msg
    assert "source email" not in msg.lower()
    assert err.existing_bill_public_id == "bill-55-secret"


def test_inaccessible_duplicate_with_source_email_does_not_link():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, repo = _bill_service(_existing_bill())
    with patch(
        "entities.bill.business.service.can_access_bill",
        return_value=False,
    ):
        _try_create(svc, source_email_message_public_id="email-1")
    repo.link_source_email_message.assert_not_called()


def test_accessible_duplicate_with_source_email_still_links_draft():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, repo = _bill_service(_existing_bill(status="in_review", is_draft=True))
    with patch("entities.bill.business.service.can_access_bill", return_value=True):
        _try_create(svc, source_email_message_public_id="email-1")
    repo.link_source_email_message.assert_called_once_with(
        bill_id=55,
        source_email_message_id=123,
    )


def test_link_failure_message_omits_exception_text():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, repo = _bill_service(_existing_bill(status="in_review", is_draft=True))
    repo.link_source_email_message.side_effect = RuntimeError("db timeout secret-detail")
    with patch("entities.bill.business.service.can_access_bill", return_value=True):
        err = _try_create(svc, source_email_message_public_id="email-1")
    msg = str(err)
    assert "secret-detail" not in msg
    assert "db timeout" not in msg
    assert "RuntimeError" not in msg
    assert "Bill.PublicId=bill-55-secret" in msg


def test_raise_workflow_error_maps_accessible_duplicate_to_409():
    msg = (
        "A bill with BillNumber 'INV-1' and this date already exists for this vendor "
        "(Bill.PublicId=bill-1, IsDraft=True). Please update the existing bill instead."
    )
    with pytest.raises(ApiError) as exc:
        raise_workflow_error(msg, "Failed to create bill")
    assert exc.value.status_code == 409


def test_raise_workflow_error_maps_generic_duplicate_to_409():
    msg = "A bill with this number already exists for this vendor and date."
    with pytest.raises(ApiError) as exc:
        raise_workflow_error(msg, "Failed to create bill")
    assert exc.value.status_code == 409


def test_complete_bill_duplicate_guard_message_carries_no_public_id():
    """Confirm-only (U-678): _assert_no_duplicate_on_complete must stay id-free."""
    svc = BillService(repo=MagicMock())
    svc.repo.read_by_bill_number_and_vendor_id.return_value = SimpleNamespace(
        public_id="other-bill",
    )
    bill = SimpleNamespace(vendor_id=1, bill_number="INV-1", bill_date="2026-09-01")
    with pytest.raises(ValueError) as exc:
        svc._assert_no_duplicate_on_complete(bill, public_id="my-bill")
    assert "Bill.PublicId" not in str(exc.value)
    assert "already exists" in str(exc.value).lower()


def _focal_attachment():
    return SimpleNamespace(
        id=10,
        public_id="att-1",
        di_invoice_number="INV-100",
        di_vendor_name="Acme",
        di_invoice_date=None,
        di_due_date=None,
        di_total_amount=None,
        di_currency=None,
        extraction_status="extracted",
    )


def _gather_context(*, access_side_effect=None, patch_can_access_at="shared.access.can_access_bill"):
    from entities.email_message.business.service import EmailMessageService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = EmailMessageService()
    svc.repo = MagicMock()
    svc.attachment_repo = MagicMock()
    email = SimpleNamespace(
        id=1,
        subject="7550 Buffalo",
        body_preview="",
        from_address="billing@acme.com",
    )
    svc.repo.read_by_id.return_value = email
    svc.attachment_repo.read_by_email_message_id.return_value = [_focal_attachment()]

    bill_access = SimpleNamespace(
        id=100,
        public_id="bill-access",
        vendor_id=1,
        bill_number="INV-100",
        bill_date="2026-01-01",
        total_amount=None,
        is_draft=True,
        source_email_message_id=None,
    )
    bill_hidden = SimpleNamespace(
        id=200,
        public_id="bill-hidden",
        vendor_id=2,
        bill_number="INV-100",
        bill_date="2026-01-01",
        total_amount=None,
        is_draft=False,
        source_email_message_id=99,
    )

    def _lookup(bill_number, vendor_id, bill_date=None):
        if vendor_id == 1:
            return bill_access
        if vendor_id == 2:
            return bill_hidden
        return None

    with patch("entities.vendor.business.service.VendorService") as mock_vendor_cls, patch(
        "entities.bill.persistence.repo.BillRepository"
    ) as mock_bill_repo_cls, patch(
        patch_can_access_at,
        side_effect=access_side_effect,
    ) as mock_access:
        mock_vendor_cls.return_value.find_for_invoice.return_value = [
            {"vendor": {"id": 1, "name": "Acme"}},
            {"vendor": {"id": 2, "name": "Other"}},
        ]
        mock_bill_repo_cls.return_value.read_by_bill_number_and_vendor_id.side_effect = (
            _lookup
        )
        result = svc.gather_invoice_context(email_message_id=1)
        return result, mock_access, bill_access, bill_hidden


def test_gather_invoice_context_omits_inaccessible_bill_matches():
    def _access(bill_id):
        if bill_id == 200:
            return False
        return True

    result, mock_access, bill_access, _hidden = _gather_context(
        access_side_effect=_access,
    )
    assert mock_access.call_count == 2
    matches = result["existing_bill_matches"]
    assert len(matches) == 1
    assert matches[0]["public_id"] == bill_access.public_id
    assert matches[0]["id"] == bill_access.id


def test_gather_invoice_context_skips_match_when_access_check_raises():
    driver_detail = "db driver leak secret-abc"

    def _check_side(_udf_name, bill_id):
        if bill_id == 200:
            raise RuntimeError(driver_detail)
        return True

    result, mock_access, bill_access, _hidden = _gather_context(
        access_side_effect=_check_side,
        patch_can_access_at="shared.access._check",
    )
    assert mock_access.call_count == 2
    matches = result["existing_bill_matches"]
    assert len(matches) == 1
    assert matches[0]["public_id"] == bill_access.public_id


def test_gather_invoice_context_system_context_still_returns_matches():
    from entities.email_message.business.service import EmailMessageService

    svc = EmailMessageService()
    svc.repo = MagicMock()
    svc.attachment_repo = MagicMock()
    email = SimpleNamespace(
        id=1,
        subject="subj",
        body_preview="",
        from_address="a@v.com",
    )
    svc.repo.read_by_id.return_value = email
    svc.attachment_repo.read_by_email_message_id.return_value = [_focal_attachment()]

    bills = [
        SimpleNamespace(
            id=100,
            public_id="bill-a",
            vendor_id=1,
            bill_number="INV-100",
            bill_date="2026-01-01",
            total_amount=None,
            is_draft=True,
            source_email_message_id=None,
        ),
        SimpleNamespace(
            id=200,
            public_id="bill-b",
            vendor_id=2,
            bill_number="INV-100",
            bill_date="2026-01-01",
            total_amount=None,
            is_draft=False,
            source_email_message_id=5,
        ),
    ]

    with patch("entities.vendor.business.service.VendorService") as mock_vendor_cls, patch(
        "entities.bill.persistence.repo.BillRepository"
    ) as mock_bill_repo_cls:
        mock_vendor_cls.return_value.find_for_invoice.return_value = [
            {"vendor": {"id": 1, "name": "V1"}},
            {"vendor": {"id": 2, "name": "V2"}},
        ]

        def _lookup(bill_number, vendor_id, bill_date=None):
            return bills[vendor_id - 1]

        mock_bill_repo_cls.return_value.read_by_bill_number_and_vendor_id.side_effect = (
            _lookup
        )
        with system_authz():
            result = svc.gather_invoice_context(email_message_id=1)
    assert len(result["existing_bill_matches"]) == 2
    public_ids = {m["public_id"] for m in result["existing_bill_matches"]}
    assert public_ids == {"bill-a", "bill-b"}
