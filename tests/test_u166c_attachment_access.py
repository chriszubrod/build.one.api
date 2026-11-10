"""U-166c — attachment access gating.

Design: docs/design/u166c-attachment-access.md (authoritative). Covers the
shared access helpers, AttachmentService by-id reads, the route-level gates
(list routes system-admin only; by-id routes 404 and never fetch the blob), and
BLIA / ELIA create (same attachment -> existing link; different attachment -> 409).

All DB access is mocked: `shared.access._check` is patched for the UDF call and
the repositories / workflow engine are MagicMocks.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from shared.access import (
    EntityNotAccessibleError,
    _ALLOWED_UDFS,
    assert_can_access_attachment,
    can_access_attachment,
)
from shared.authz import clear_authz_context, set_authz_context, system_authz
from entities.attachment.business.service import AttachmentService
from shared.api.responses import ATTACHMENT_LINK_CONFLICT_PREFIX
from entities.auth.business.service import get_current_user_api

import entities.attachment.api.router as attachment_router
import entities.bill_line_item_attachment.api.router as blia_router
import entities.expense_line_item_attachment.api.router as elia_router
import entities.bill_line_item_attachment.business.service as blia_service_mod
import entities.expense_line_item_attachment.business.service as elia_service_mod
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
from app import app

ACTOR_ID = 20
UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _row(id_=5, public_id=UUID_A):
    return SimpleNamespace(
        id=id_,
        public_id=public_id,
        is_archived=False,
        blob_url="https://buildone.blob.core.windows.net/attachments/x.pdf",
        content_type="application/pdf",
        original_filename="x.pdf",
        filename="x.pdf",
        file_extension="pdf",
        created_by_user_id=99,
    )


def _override_module_dep(handler, user):
    """Override the require_module_api dependency a route declares."""
    dep = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: user


# ---------------------------------------------------------------------------
# shared.access — the UDF whitelist and the can_/assert_ pair
# ---------------------------------------------------------------------------


def test_attachment_udf_is_whitelisted():
    assert "UserCanAccessAttachment" in _ALLOWED_UDFS


def test_can_access_attachment_bypasses_for_system_admin():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=True)
    with patch("shared.access._check") as mock_check:
        assert can_access_attachment(55) is True
    mock_check.assert_not_called()


def test_assert_can_access_attachment_bypasses_for_system_admin():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=True)
    with patch("shared.access._check") as mock_check:
        assert assert_can_access_attachment(55) is None
    mock_check.assert_not_called()


def test_none_attachment_id_is_denied_by_boolean_and_ignored_by_assert():
    for admin in (False, True):
        set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=admin)
        with patch("shared.access._check") as mock_check:
            assert can_access_attachment(None) is False
            assert assert_can_access_attachment(None) is None
        mock_check.assert_not_called()


def test_can_access_attachment_true_when_udf_allows():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=True) as mock_check:
        assert can_access_attachment(55) is True
    mock_check.assert_called_once_with("UserCanAccessAttachment", 55)


def test_assert_can_access_attachment_passes_when_udf_allows():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=True) as mock_check:
        assert_can_access_attachment(55)
    mock_check.assert_called_once_with("UserCanAccessAttachment", 55)


def test_assert_can_access_attachment_denial_raises_not_accessible():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=False):
        with pytest.raises(EntityNotAccessibleError) as exc:
            assert_can_access_attachment(55)
    assert exc.value.entity == "Attachment"
    assert exc.value.entity_id == 55


def test_can_access_attachment_false_when_udf_denies():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    with patch("shared.access._check", return_value=False):
        assert can_access_attachment(55) is False


def test_can_access_attachment_fails_closed_on_check_error():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    with patch("shared.access._check", side_effect=RuntimeError("driver text")):
        assert can_access_attachment(55) is False


# ---------------------------------------------------------------------------
# AttachmentService — by-id reads are gated; list/hash reads are not
# ---------------------------------------------------------------------------


def _service(row):
    repo = MagicMock()
    repo.read_by_public_id.return_value = row
    repo.read_by_id.return_value = row
    return AttachmentService(repo=repo), repo


def test_service_read_by_public_id_denied_raises():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    svc, _ = _service(_row())
    with patch("shared.access._check", return_value=False) as mock_check:
        with pytest.raises(EntityNotAccessibleError):
            svc.read_by_public_id(public_id=UUID_A)
    mock_check.assert_called_once_with("UserCanAccessAttachment", 5)


def test_service_read_by_id_denied_raises():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    svc, _ = _service(_row())
    with patch("shared.access._check", return_value=False):
        with pytest.raises(EntityNotAccessibleError):
            svc.read_by_id(id=5)


def test_service_read_by_public_id_allowed_returns_row():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    row = _row()
    svc, _ = _service(row)
    with patch("shared.access._check", return_value=True):
        assert svc.read_by_public_id(public_id=UUID_A) is row


def test_service_read_by_id_allowed_returns_row():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    row = _row()
    svc, _ = _service(row)
    with patch("shared.access._check", return_value=True):
        assert svc.read_by_id(id=5) is row


def test_service_not_found_returns_none_without_access_check():
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    svc, _ = _service(None)
    with patch("shared.access._check") as mock_check:
        assert svc.read_by_public_id(public_id=UUID_A) is None
        assert svc.read_by_id(id=5) is None
    mock_check.assert_not_called()


def test_service_system_context_skips_access_check():
    row = _row()
    svc, _ = _service(row)
    with system_authz():
        with patch("shared.access._check") as mock_check:
            assert svc.read_by_public_id(public_id=UUID_A) is row
            assert svc.read_by_id(id=5) is row
    mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/get/attachments",
        "/api/v1/get/attachments/by-category/receipt",
        "/api/v1/get/attachments/by-hash/" + "a" * 64,
    ],
)
def test_list_routes_refuse_non_admin(client, path):
    # A non-admin who HOLDS ATTACHMENTS read: the module gate is granted, so only the
    # system-admin requirement can refuse. Without this the 403 could come from the
    # module check and the test would not prove the admin gate.
    app.dependency_overrides[get_current_user_api] = lambda: {
        "id": ACTOR_ID,
        "tenant_id": 1,
        "is_system_admin": False,
    }
    with patch("shared.rbac._module_permission_denial", return_value=None), patch.object(
        attachment_router.service, "read_all", return_value=[]
    ), patch.object(attachment_router.service, "read_by_category", return_value=[]), patch.object(
        attachment_router.service, "read_by_hash", return_value=None
    ):
        response = client.get(path)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/get/attachments",
        "/api/v1/get/attachments/by-category/receipt",
    ],
)
def test_list_routes_allow_system_admin(client, path):
    app.dependency_overrides[get_current_user_api] = lambda: {
        "id": 17,
        "tenant_id": 1,
        "is_system_admin": True,
    }
    with patch.object(attachment_router.service, "read_all", return_value=[]), patch.object(
        attachment_router.service, "read_by_category", return_value=[]
    ):
        response = client.get(path)
    assert response.status_code == 200


def test_view_inaccessible_attachment_is_404_and_blob_never_fetched(client):
    _override_module_dep(attachment_router.view_attachment_router, {"id": ACTOR_ID, "tenant_id": 1})
    with patch.object(attachment_router.service.repo, "read_by_public_id", return_value=_row()), patch(
        "shared.access._check", return_value=False
    ), patch.object(attachment_router, "AzureBlobStorage") as storage:
        response = client.get(f"/api/v1/view/attachment/{UUID_A}")
    assert response.status_code == 404
    storage.assert_not_called()


def test_download_inaccessible_attachment_is_404_and_blob_never_fetched(client):
    _override_module_dep(attachment_router.download_attachment_router, {"id": ACTOR_ID, "tenant_id": 1})
    with patch.object(attachment_router.service.repo, "read_by_public_id", return_value=_row()) as read, patch(
        "shared.access._check", return_value=False
    ), patch.object(attachment_router.service.repo, "increment_download_count") as bump, patch.object(
        attachment_router, "AzureBlobStorage"
    ) as storage:
        response = client.get(f"/api/v1/download/attachment/{UUID_A}")
    assert response.status_code == 404
    read.assert_called_once()
    storage.assert_not_called()
    bump.assert_not_called()


def test_get_by_public_id_inaccessible_is_404(client):
    _override_module_dep(attachment_router.get_attachment_by_public_id_router, {"id": ACTOR_ID, "tenant_id": 1})
    with patch.object(attachment_router.service.repo, "read_by_public_id", return_value=_row()), patch(
        "shared.access._check", return_value=False
    ):
        response = client.get(f"/api/v1/get/attachment/{UUID_A}")
    assert response.status_code == 404


def test_view_accessible_attachment_fetches_blob(client):
    _override_module_dep(attachment_router.view_attachment_router, {"id": ACTOR_ID, "tenant_id": 1})
    storage = MagicMock()
    storage.download_file.return_value = (b"%PDF", {"content_type": "application/pdf"})
    with patch.object(attachment_router.service.repo, "read_by_public_id", return_value=_row()), patch(
        "shared.access._check", return_value=True
    ), patch.object(attachment_router, "AzureBlobStorage", return_value=storage):
        response = client.get(f"/api/v1/view/attachment/{UUID_A}")
    assert response.status_code == 200
    storage.download_file.assert_called_once()


# ---------------------------------------------------------------------------
# BLIA / ELIA create — same attachment returns existing; different one conflicts
# ---------------------------------------------------------------------------


def _patched_line_and_attachment(module, *, line_attr, attachment_id):
    line_service = MagicMock()
    line_service.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=3, bill_id=9, expense_id=9
    )
    attachment_service = MagicMock()
    attachment_service.return_value.read_by_public_id.return_value = SimpleNamespace(id=attachment_id)
    return (
        patch.object(module, line_attr, line_service),
        patch.object(module, "AttachmentService", attachment_service),
    )


def test_blia_create_same_attachment_returns_existing_link():
    existing = SimpleNamespace(attachment_id=7, bill_line_item_id=3)
    repo = MagicMock()
    repo.read_by_bill_line_item_id.return_value = existing
    svc = BillLineItemAttachmentService(repo=repo)
    line_patch, att_patch = _patched_line_and_attachment(
        blia_service_mod,
        line_attr="BillLineItemService",
        attachment_id=7,
    )
    with line_patch, att_patch:
        result = svc.create(bill_line_item_public_id=UUID_A, attachment_public_id=UUID_B)
    assert result is existing
    repo.create.assert_not_called()


def test_blia_create_different_attachment_conflicts_and_creates_nothing():
    existing = SimpleNamespace(attachment_id=7, bill_line_item_id=3)
    repo = MagicMock()
    repo.read_by_bill_line_item_id.return_value = existing
    svc = BillLineItemAttachmentService(repo=repo)
    line_patch, att_patch = _patched_line_and_attachment(
        blia_service_mod,
        line_attr="BillLineItemService",
        attachment_id=8,
    )
    with line_patch, att_patch:
        with pytest.raises(Exception) as exc:
            svc.create(bill_line_item_public_id=UUID_A, attachment_public_id=UUID_B)
    assert str(exc.value).startswith(ATTACHMENT_LINK_CONFLICT_PREFIX)
    repo.create.assert_not_called()


def test_blia_create_no_existing_link_creates():
    repo = MagicMock()
    repo.read_by_bill_line_item_id.return_value = None
    svc = BillLineItemAttachmentService(repo=repo)
    line_patch, att_patch = _patched_line_and_attachment(
        blia_service_mod,
        line_attr="BillLineItemService",
        attachment_id=8,
    )
    with line_patch, att_patch:
        svc.create(bill_line_item_public_id=UUID_A, attachment_public_id=UUID_B)
    repo.create.assert_called_once()


def test_elia_create_same_attachment_returns_existing_link():
    existing = SimpleNamespace(attachment_id=7, expense_line_item_id=3)
    repo = MagicMock()
    repo.read_by_expense_line_item_id.return_value = existing
    svc = ExpenseLineItemAttachmentService(repo=repo)
    line_patch, att_patch = _patched_line_and_attachment(
        elia_service_mod,
        line_attr="ExpenseLineItemService",
        attachment_id=7,
    )
    with line_patch, att_patch:
        result = svc.create(expense_line_item_public_id=UUID_A, attachment_public_id=UUID_B)
    assert result is existing
    repo.create.assert_not_called()


def test_elia_create_different_attachment_conflicts_and_creates_nothing():
    existing = SimpleNamespace(attachment_id=7, expense_line_item_id=3)
    repo = MagicMock()
    repo.read_by_expense_line_item_id.return_value = existing
    svc = ExpenseLineItemAttachmentService(repo=repo)
    line_patch, att_patch = _patched_line_and_attachment(
        elia_service_mod,
        line_attr="ExpenseLineItemService",
        attachment_id=8,
    )
    with line_patch, att_patch:
        with pytest.raises(Exception) as exc:
            svc.create(expense_line_item_public_id=UUID_A, attachment_public_id=UUID_B)
    assert str(exc.value).startswith(ATTACHMENT_LINK_CONFLICT_PREFIX)
    repo.create.assert_not_called()


def test_elia_create_no_existing_link_creates():
    repo = MagicMock()
    repo.read_by_expense_line_item_id.return_value = None
    svc = ExpenseLineItemAttachmentService(repo=repo)
    line_patch, att_patch = _patched_line_and_attachment(
        elia_service_mod,
        line_attr="ExpenseLineItemService",
        attachment_id=8,
    )
    with line_patch, att_patch:
        svc.create(expense_line_item_public_id=UUID_A, attachment_public_id=UUID_B)
    repo.create.assert_called_once()


CONFLICT_ERROR = ATTACHMENT_LINK_CONFLICT_PREFIX + "the line keeps its existing attachment"


@pytest.mark.parametrize(
    "router_mod,path,body",
    [
        pytest.param(
            blia_router,
            "/api/v1/create/bill-line-item-attachment",
            {"bill_line_item_public_id": UUID_A, "attachment_public_id": UUID_B},
            id="blia",
        ),
        pytest.param(
            elia_router,
            "/api/v1/create/expense-line-item-attachment",
            {"expense_line_item_public_id": UUID_A, "attachment_public_id": UUID_B},
            id="elia",
        ),
    ],
)
def test_link_create_conflict_maps_to_409(client, router_mod, path, body):
    handler = (
        router_mod.create_bill_line_item_attachment_router
        if router_mod is blia_router
        else router_mod.create_expense_line_item_attachment_router
    )
    _override_module_dep(handler, {"id": ACTOR_ID, "tenant_id": 1})
    engine = MagicMock()
    engine.return_value.execute_synchronous.return_value = {"success": False, "error": CONFLICT_ERROR}
    with patch.object(router_mod, "ProcessEngine", engine), patch.object(
        router_mod, "AttachmentService"
    ), patch.object(router_mod, "BillLineItemService" if router_mod is blia_router else "ExpenseLineItemService"):
        response = client.post(path, json=body)
    assert response.status_code == 409
