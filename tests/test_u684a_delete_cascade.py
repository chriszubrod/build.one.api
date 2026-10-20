"""U-684a: guarded atomic delete cascades for BillCredit and Invoice."""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit.persistence.repo import BillCreditRepository
from entities.invoice.business.service import InvoiceService
from entities.invoice.persistence.repo import InvoiceRepository
from shared.api.responses import ErrorCode, STATUS_LOCKED_PREFIX
from shared.authz import set_authz_context
from shared.lifecycle.terminal_lock import StatusLockedError


def _bill_credit(*, is_draft=True, qbo_id=None):
    return SimpleNamespace(
        id=99,
        public_id="bc-pub",
        is_draft=is_draft,
        status=None,
        qbo_id=qbo_id,
    )


def _invoice(*, is_draft=True, qbo_id=None):
    return SimpleNamespace(
        id=88,
        public_id="inv-pub",
        is_draft=is_draft,
        status=None,
        qbo_id=qbo_id,
    )


# --- BillCredit service ---


def test_bill_credit_draft_delete_calls_cascade_once():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    existing = _bill_credit(is_draft=True)
    repo = MagicMock()
    repo.delete_cascade_by_id.return_value = existing
    svc = BillCreditService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with patch(
        "entities.bill_credit_line_item.business.service.BillCreditLineItemService"
    ) as MockLI, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAtt, patch("shared.storage.AzureBlobStorage") as MockStorage:
        result = svc.delete_by_public_id("bc-pub")

    assert result is existing
    repo.delete_cascade_by_id.assert_called_once_with(99)
    repo.delete_by_id.assert_not_called()
    MockLI.assert_not_called()
    MockAtt.assert_not_called()
    MockStorage.assert_not_called()


@pytest.mark.parametrize("is_admin", [False, True])
def test_bill_credit_non_draft_refused_for_all_roles(is_admin):
    set_authz_context(user_id=20, company_id=1, is_system_admin=is_admin)
    existing = _bill_credit(is_draft=False)
    repo = MagicMock()
    svc = BillCreditService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with pytest.raises(StatusLockedError, match="cannot be deleted"):
        svc.delete_by_public_id("bc-pub")
    repo.delete_cascade_by_id.assert_not_called()


@pytest.mark.parametrize("is_admin", [False, True])
def test_bill_credit_qbo_id_refused_for_all_roles(is_admin):
    set_authz_context(user_id=20, company_id=1, is_system_admin=is_admin)
    existing = _bill_credit(is_draft=True, qbo_id="123")
    repo = MagicMock()
    svc = BillCreditService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with pytest.raises(ValueError, match="QuickBooks"):
        svc.delete_by_public_id("bc-pub")
    repo.delete_cascade_by_id.assert_not_called()


def test_bill_credit_citation_refusal_from_repo():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    existing = _bill_credit(is_draft=True)
    repo = MagicMock()
    repo.delete_cascade_by_id.side_effect = ValueError(
        "Cannot delete this bill credit: invoice INV-1 still cites one of its line items."
    )
    svc = BillCreditService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with pytest.raises(ValueError, match="INV-1"):
        svc.delete_by_public_id("bc-pub")


def test_bill_credit_missing_returns_none():
    svc = BillCreditService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=None)
    assert svc.delete_by_public_id("missing") is None


def test_invoice_draft_delete_calls_cascade_once():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    existing = _invoice(is_draft=True)
    repo = MagicMock()
    repo.delete_cascade_by_id.return_value = existing
    svc = InvoiceService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with patch(
        "entities.invoice_line_item.business.service.InvoiceLineItemService"
    ) as MockILI, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAtt, patch("shared.storage.AzureBlobStorage") as MockStorage:
        result = svc.delete_by_public_id("inv-pub")

    assert result is existing
    repo.delete_cascade_by_id.assert_called_once_with(88)
    repo.delete_by_id.assert_not_called()
    MockILI.assert_not_called()
    MockAtt.assert_not_called()
    MockStorage.assert_not_called()


@pytest.mark.parametrize("is_admin", [False, True])
def test_invoice_non_draft_refused_for_all_roles(is_admin):
    set_authz_context(user_id=20, company_id=1, is_system_admin=is_admin)
    existing = _invoice(is_draft=False)
    repo = MagicMock()
    svc = InvoiceService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with pytest.raises(StatusLockedError, match="cannot be deleted"):
        svc.delete_by_public_id("inv-pub")
    repo.delete_cascade_by_id.assert_not_called()


@pytest.mark.parametrize("is_admin", [False, True])
def test_invoice_qbo_id_refused_for_all_roles(is_admin):
    set_authz_context(user_id=20, company_id=1, is_system_admin=is_admin)
    existing = _invoice(is_draft=True, qbo_id="456")
    repo = MagicMock()
    svc = InvoiceService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with pytest.raises(ValueError, match="QuickBooks"):
        svc.delete_by_public_id("inv-pub")
    repo.delete_cascade_by_id.assert_not_called()


def test_invoice_missing_returns_none():
    svc = InvoiceService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=None)
    assert svc.delete_by_public_id("missing") is None


def test_invoice_delete_does_not_call_reset_source_as_unbilled():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    existing = _invoice(is_draft=True)
    repo = MagicMock()
    repo.delete_cascade_by_id.return_value = existing
    svc = InvoiceService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)
    svc._reset_source_as_unbilled = MagicMock()

    svc.delete_by_public_id("inv-pub")
    svc._reset_source_as_unbilled.assert_not_called()


# --- Repo error mapping ---


def test_bill_credit_repo_maps_status_locked():
    import entities.bill_credit.persistence.repo as mod

    with patch.object(mod, "call_procedure", side_effect=Exception("[42000] STATUS_LOCKED: x")), patch.object(
        mod, "get_connection"
    ):
        with pytest.raises(StatusLockedError, match="cannot be deleted"):
            BillCreditRepository().delete_cascade_by_id(1)


def test_invoice_repo_maps_qbo_refusal():
    import entities.invoice.persistence.repo as mod

    with patch.object(
        mod,
        "call_procedure",
        side_effect=Exception(
            "[42000] This invoice came from QuickBooks; delete it in QuickBooks instead. (50000)"
        ),
    ), patch.object(mod, "get_connection"):
        with pytest.raises(ValueError, match="QuickBooks"):
            InvoiceRepository().delete_cascade_by_id(1)


# --- Routers ---


@pytest.fixture
def api_client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _override_dep(route_fn):
    dep = inspect.signature(route_fn).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"tenant_id": 1, "id": 20}


def test_bill_credit_delete_router_maps_status_locked(api_client):
    from entities.bill_credit.api.router import delete_bill_credit_by_public_id_router

    _override_dep(delete_bill_credit_by_public_id_router)
    with patch("entities.bill_credit.api.router.ProcessEngine") as mock_pe:
        mock_pe.return_value.execute_synchronous.return_value = {
            "success": False,
            "error": f"{STATUS_LOCKED_PREFIX}it cannot be deleted",
        }
        resp = api_client.delete(
            "/api/v1/delete/bill-credit/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == ErrorCode.STATUS_LOCKED
    assert "pyodbc" not in resp.text.lower()


def test_bill_credit_delete_router_maps_qbo_refusal(api_client):
    from entities.bill_credit.api.router import delete_bill_credit_by_public_id_router

    from entities.bill_credit.business.service import QBO_DELETE_REFUSAL

    msg = QBO_DELETE_REFUSAL
    _override_dep(delete_bill_credit_by_public_id_router)
    with patch("entities.bill_credit.api.router.ProcessEngine") as mock_pe:
        mock_pe.return_value.execute_synchronous.return_value = {
            "success": False,
            "error": msg,
        }
        resp = api_client.delete(
            "/api/v1/delete/bill-credit/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == msg


def test_invoice_delete_router_maps_qbo_refusal(api_client):
    from entities.invoice.api.router import delete_invoice_by_public_id_router

    from entities.invoice.business.service import QBO_DELETE_REFUSAL

    msg = QBO_DELETE_REFUSAL
    _override_dep(delete_invoice_by_public_id_router)
    with patch(
        "entities.invoice.api.router.InvoiceService"
    ) as mock_svc_cls:
        mock_svc_cls.return_value.delete_by_public_id.side_effect = ValueError(msg)
        resp = api_client.delete(
            "/api/v1/delete/invoice/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == msg
    assert "pyodbc" not in resp.text.lower()


# --- Static sproc shape ---


def test_bill_credit_cascade_sproc_shape():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/bill_credit/sql/dbo.bill_credit.sql", "DeleteBillCreditCascadeById")
    lock_at = body.index("WITH (UPDLOCK, HOLDLOCK)")
    draft_refuse = body.index("a non-draft Bill Credit cannot be deleted")
    first_delete = body.index("DELETE")
    assert lock_at < draft_refuse < first_delete
    assert "DELETE FROM dbo.[Review]" in body
    assert "DELETE FROM dbo.[Attachment]" not in body


def test_invoice_cascade_sproc_shape():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/invoice/sql/dbo.invoice.sql", "DeleteInvoiceCascadeById")
    assert "InvoiceLineItemSourceProvenance" in body
    assert "DELETE FROM dbo.[InvoiceAttachment]" in body
    assert "DELETE FROM dbo.[Attachment]" not in body
