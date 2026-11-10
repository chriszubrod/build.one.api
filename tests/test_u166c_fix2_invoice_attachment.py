"""U-166c fix round 2 (Pass 3 HIGH) — raw-id invoice_attachment link writes.

Exploit: POST /create/invoice_attachment took a raw invoice_id and attachment_id
and wrote the link with no access check. dbo.UserCanAccessAttachment grants read
through InvoiceAttachment -> Invoice.ProjectId, so a user could plant a link on an
invoice they can see and then read ANY attachment by its sequential id.

Fix: the invoice_attachment routes are system-admin only (no client uses them),
the service re-checks both sides through their gated reads (defense in depth; the
packet generator is the only internal caller), and the ILIA delete is gated on
the line's invoice.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from entities.invoice_attachment.business.service import InvoiceAttachmentService
from entities.invoice_line_item_attachment.business.service import InvoiceLineItemAttachmentService
from shared.access import EntityNotAccessibleError
from shared.authz import clear_authz_context, set_authz_context

import entities.invoice_attachment.api.router as ia_router
import entities.invoice_line_item_attachment.api.router as ilia_router

ACTOR_ID = 20
UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _clean():
    clear_authz_context()
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    yield
    clear_authz_context()
    app.dependency_overrides.clear()


def _override_non_admin(handler):
    """A signed-in non-admin. The route's own admin dependency is NOT overridden, so the
    403 comes from require_system_admin itself."""
    app.dependency_overrides[get_current_user_api] = lambda: {
        "id": ACTOR_ID, "tenant_id": 1, "is_system_admin": False,
    }


def _guard_qualname(handler):
    dep = inspect.signature(handler).parameters["current_user"].default.dependency
    return dep.__qualname__


def _att(id_=99, created_by=ACTOR_ID):
    return SimpleNamespace(id=id_, public_id=UUID_A, created_by_user_id=created_by, blob_url="https://b/x.pdf")


def _service_mocks(*, attachment):
    """Real InvoiceService / AttachmentService gates; only the repos are mocked."""
    inv_repo = MagicMock()
    inv_repo.read_by_id.return_value = SimpleNamespace(id=1, project_id=7)
    att_repo = MagicMock()
    att_repo.read_by_id.return_value = attachment
    link_repo = MagicMock()
    return (
        patch("entities.invoice.business.service.InvoiceRepository", return_value=inv_repo),
        patch("entities.attachment.business.service.AttachmentRepository", return_value=att_repo),
        link_repo,
    )


# ---------------------------------------------------------------------------
# 1. Routes are system-admin only (the exploit's entry point)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body,handler",
    [
        pytest.param("POST", "/api/v1/create/invoice_attachment", {"invoice_id": 1, "attachment_id": 99},
                     ia_router.create_invoice_attachment_router, id="create"),
        pytest.param("GET", "/api/v1/get/invoice_attachments", None,
                     ia_router.get_invoice_attachments_router, id="list"),
        pytest.param("GET", f"/api/v1/get/invoice_attachment/{UUID_A}", None,
                     ia_router.get_invoice_attachment_by_public_id_router, id="get"),
        pytest.param("GET", "/api/v1/get/invoice_attachments/invoice/1", None,
                     ia_router.get_invoice_attachments_by_invoice_id_router, id="by_invoice"),
        pytest.param("DELETE", f"/api/v1/delete/invoice_attachment/{UUID_A}", None,
                     ia_router.delete_invoice_attachment_by_public_id_router, id="delete"),
    ],
)
def test_invoice_attachment_routes_refuse_non_admin(method, path, body, handler):
    assert "require_system_admin" in _guard_qualname(handler), "route is not system-admin only"
    _override_non_admin(handler)
    with patch.object(InvoiceAttachmentService, "create", side_effect=AssertionError("reached service")) as create:
        response = TestClient(app, raise_server_exceptions=False).request(method, path, json=body)
    assert response.status_code == 403
    create.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Service-level exploit: an accessible invoice, an inaccessible attachment
# ---------------------------------------------------------------------------


def test_service_create_refuses_inaccessible_attachment_and_writes_nothing():
    link_repo = MagicMock()
    inv_patch, att_patch, _ = _service_mocks(attachment=_att(created_by=55))
    with inv_patch, att_patch, patch(
        "shared.access._check",
        side_effect=lambda udf, eid: udf == "UserCanAccessProject",
    ):
        with pytest.raises(EntityNotAccessibleError):
            InvoiceAttachmentService(repo=link_repo).create(invoice_id=1, attachment_id=99)
    link_repo.create.assert_not_called()


def test_service_create_refuses_inaccessible_invoice_and_writes_nothing():
    link_repo = MagicMock()
    inv_patch, att_patch, _ = _service_mocks(attachment=_att())
    with inv_patch, att_patch, patch("shared.access._check", return_value=False):
        with pytest.raises(EntityNotAccessibleError):
            InvoiceAttachmentService(repo=link_repo).create(invoice_id=1, attachment_id=99)
    link_repo.create.assert_not_called()


def test_packet_path_still_links_its_own_attachment_for_an_authorized_user():
    """The packet generator creates the attachment as the requesting user and links it
    to an invoice that user can open. Both gated reads pass, so the link is written."""
    link_repo = MagicMock()
    inv_patch, att_patch, _ = _service_mocks(attachment=_att(created_by=ACTOR_ID))
    with inv_patch, att_patch, patch("shared.access._check", return_value=True):
        InvoiceAttachmentService(repo=link_repo).create(invoice_id=1, attachment_id=99)
    link_repo.create.assert_called_once_with(invoice_id=1, attachment_id=99)


# ---------------------------------------------------------------------------
# 3. ILIA delete is gated on the line's invoice; denial -> 404, nothing deleted
# ---------------------------------------------------------------------------


def _ilia_mocks(link_repo):
    link_repo.read_by_public_id.return_value = SimpleNamespace(id=9, invoice_line_item_id=3)
    ili_repo = MagicMock()
    ili_repo.read_by_id.return_value = SimpleNamespace(id=3, invoice_id=1)
    inv_repo = MagicMock()
    inv_repo.read_by_id.return_value = SimpleNamespace(id=1, project_id=7)
    return (
        patch("entities.invoice_line_item.business.service.InvoiceLineItemRepository", return_value=ili_repo),
        patch("entities.invoice_line_item.business.service.InvoiceRepository", return_value=inv_repo),
    )


def test_ilia_service_delete_denied_when_invoice_inaccessible_deletes_nothing():
    link_repo = MagicMock()
    ili_patch, inv_patch = _ilia_mocks(link_repo)
    with ili_patch, inv_patch, patch("shared.access._check", return_value=False):
        with pytest.raises(EntityNotAccessibleError):
            InvoiceLineItemAttachmentService(repo=link_repo).delete_by_public_id(public_id=UUID_A)
    link_repo.delete_by_id.assert_not_called()


def test_ilia_delete_route_denial_is_404_and_deletes_nothing():
    link_repo = MagicMock()
    ili_patch, inv_patch = _ilia_mocks(link_repo)
    # The ILIA delete is module-gated (not admin-only): a user who holds the module
    # reaches the service, so the denial under test is the invoice gate itself.
    handler = ilia_router.delete_invoice_line_item_attachment_by_public_id_router
    dep = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"id": ACTOR_ID, "tenant_id": 1}
    with ili_patch, inv_patch, patch.object(ilia_router.service, "repo", link_repo), patch(
        "shared.access._check", return_value=False
    ):
        response = TestClient(app, raise_server_exceptions=False).delete(
            f"/api/v1/delete/invoice-line-item-attachment/{UUID_A}"
        )
    assert response.status_code == 404
    link_repo.delete_by_id.assert_not_called()


def test_service_create_refuses_denied_invoice_even_when_attachment_is_allowed():
    """Mutation gap: the invoice gate must refuse on its own. Here the attachment
    gate ALLOWS, so only the invoice read can refuse."""
    link_repo = MagicMock()
    inv_patch, att_patch, _ = _service_mocks(attachment=_att(created_by=ACTOR_ID))
    with inv_patch, att_patch, patch(
        "shared.access._check",
        side_effect=lambda udf, eid: udf == "UserCanAccessAttachment",
    ):
        with pytest.raises(EntityNotAccessibleError):
            InvoiceAttachmentService(repo=link_repo).create(invoice_id=1, attachment_id=99)
    link_repo.create.assert_not_called()


def test_ilia_delete_refuses_when_the_link_line_is_missing():
    """Mutation gap: a link whose line is gone must be refused (fail closed), not
    deleted. Access is granted, so only the missing-line branch can refuse."""
    link_repo = MagicMock()
    ili_patch, inv_patch = _ilia_mocks(link_repo)
    with ili_patch as ili_cls, inv_patch, patch("shared.access._check", return_value=True):
        ili_cls.return_value.read_by_id.return_value = None
        with pytest.raises(EntityNotAccessibleError):
            InvoiceLineItemAttachmentService(repo=link_repo).delete_by_public_id(public_id=UUID_A)
    link_repo.delete_by_id.assert_not_called()
