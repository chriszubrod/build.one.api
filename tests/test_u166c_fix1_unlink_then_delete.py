"""U-166c fix round 1 — F1/F2/F3 and the leg-B denial matrix.

F1: unlink-then-delete. Callers delete the attachment LINK row first (FK), so a
gated re-read of the Attachment AFTER the unlink is refused for any actor who is
not the uploader, and the row + blob are left behind. The fix authorizes while
the link still exists and then deletes via AttachmentService.delete_authorized,
which keeps the completed-parent guard but does not re-gate.

Access is simulated with a link store: `shared.access._check` allows an
attachment only while its link row still exists, so any gated read after the
unlink is denied (exactly the production failure).
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.attachment.business.service import AttachmentService
from shared.api.responses import ATTACHMENT_LINK_CONFLICT_PREFIX
from entities.auth.business.service import get_current_user_api
from shared.access import EntityNotAccessibleError
from shared.authz import clear_authz_context, set_authz_context
from shared.lifecycle.terminal_lock import StatusLockedError

import entities.attachment.api.router as attachment_router
import entities.bill_line_item_attachment.api.router as blia_router
import entities.expense_line_item_attachment.api.router as elia_router
import entities.invoice_line_item_attachment.api.router as ilia_router

ACTOR_ID = 20
UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BLOB = "https://buildone.blob.core.windows.net/attachments/x.pdf"


@pytest.fixture(autouse=True)
def _clean():
    clear_authz_context()
    set_authz_context(user_id=ACTOR_ID, company_id=1, is_system_admin=False)
    yield
    clear_authz_context()
    app.dependency_overrides.clear()


def _att(id_=5, public_id=UUID_A):
    return SimpleNamespace(
        id=id_, public_id=public_id, is_archived=False, blob_url=BLOB,
        content_type="application/pdf", original_filename="x.pdf", filename="x.pdf",
        file_extension="pdf", created_by_user_id=99, category="invoice_packet",
    )


class LinkStore:
    """Attachment access exists only while its link row exists."""

    def __init__(self):
        self.linked = True

    def check(self, udf_name, entity_id):
        return self.linked


@pytest.fixture
def store():
    s = LinkStore()
    with patch("shared.access._check", side_effect=s.check):
        yield s


@pytest.fixture
def att_repo():
    """AttachmentService's repo, with no DB."""
    repo = MagicMock()
    repo.read_by_id.return_value = _att()
    repo.read_by_public_id.return_value = _att()
    repo.delete_by_id.return_value = _att()
    with patch("entities.attachment.business.service.AttachmentRepository", return_value=repo):
        yield repo


@pytest.fixture
def blob():
    with patch("shared.storage.AzureBlobStorage") as storage:
        yield storage


# ---------------------------------------------------------------------------
# F1 — the service method
# ---------------------------------------------------------------------------


def test_delete_authorized_deletes_without_regating(store, att_repo):
    store.linked = False  # link already gone: a gated read would be refused
    svc = AttachmentService(repo=att_repo)
    with patch.object(AttachmentService, "is_evidence_for_a_completed_parent", return_value=False), patch(
        "shared.access._check", side_effect=AssertionError("re-gated")
    ) as check:
        svc.delete_authorized(_att())
    check.assert_not_called()
    att_repo.delete_by_id.assert_called_once()


def test_delete_authorized_keeps_completed_parent_guard(store, att_repo):
    svc = AttachmentService(repo=att_repo)
    with patch.object(AttachmentService, "is_evidence_for_a_completed_parent", return_value=True):
        with pytest.raises(StatusLockedError):
            svc.delete_authorized(_att())
    att_repo.delete_by_id.assert_not_called()


def test_delete_by_public_id_stays_gated_for_the_http_route(store, att_repo):
    store.linked = False
    svc = AttachmentService(repo=att_repo)
    with pytest.raises(EntityNotAccessibleError):
        svc.delete_by_public_id(public_id=UUID_A)
    att_repo.delete_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# F1 — the four callers, driven for real, with a non-uploader who loses the
# link mid-cascade. The gate must be consulted only while the link exists.
# ---------------------------------------------------------------------------


def test_ili_cascade_purges_attachment_after_unlink(store, att_repo, blob):
    """InvoiceLineItemService.delete_by_public_id: read the Attachment, drop the
    join row, then purge the Attachment and blob. Must not re-gate after unlink."""
    from entities.invoice_line_item.business.service import InvoiceLineItemService

    ilia_repo = MagicMock()
    ilia_repo.read_by_invoice_line_item_id.return_value = [SimpleNamespace(id=9, attachment_id=5)]
    ilia_repo.delete_by_id.side_effect = lambda *_a, **_k: setattr(store, "linked", False)
    ili_repo = MagicMock()
    ili_svc = InvoiceLineItemService(repo=ili_repo)
    ili_repo.read_by_public_id.return_value = SimpleNamespace(id=2, public_id=UUID_B)
    with patch.object(AttachmentService, "is_evidence_for_a_completed_parent", return_value=False), patch.object(InvoiceLineItemService, "read_by_public_id", return_value=SimpleNamespace(id=2)), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_cls:
        ilia_cls.return_value.repo = ilia_repo
        ili_svc.delete_by_public_id(public_id=UUID_B)
    att_repo.delete_by_id.assert_called_once()
    blob.assert_called()


def test_eli_cascade_purges_attachment_after_unlink(store, att_repo, blob):
    """ExpenseLineItemService.delete_by_public_id: read the Attachment, drop the
    link row, then purge the Attachment and blob. Must not re-gate after unlink."""
    from entities.expense_line_item.business.service import ExpenseLineItemService

    link_repo = MagicMock()
    link_repo.read_by_expense_line_item_id.return_value = SimpleNamespace(id=9, attachment_id=5)
    link_repo.delete_by_id.side_effect = lambda *_a, **_k: setattr(store, "linked", False)
    eli_svc = ExpenseLineItemService(repo=MagicMock())
    with patch.object(AttachmentService, "is_evidence_for_a_completed_parent", return_value=False), patch.object(ExpenseLineItemService, "read_by_public_id", return_value=SimpleNamespace(id=2, expense_id=1)), patch.object(
        ExpenseLineItemService, "_assert_parent_editable", create=True
    ), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository",
        return_value=link_repo,
    ):
        eli_svc.delete_by_public_id(public_id=UUID_B)
    att_repo.delete_by_id.assert_called_once()
    blob.assert_called()


def test_invoice_packet_regenerate_purges_old_packet_after_unlink():
    """The packet regenerate path reads the old packet Attachment before the
    InvoiceAttachment link is deleted, then purges it via delete_authorized.
    Structural pin: the purge must not call the gated delete by public id."""
    import entities.invoice.api.router as invoice_router

    src = inspect.getsource(invoice_router._generate_invoice_packet)
    assert "delete_by_public_id(old_att.public_id)" not in src
    assert "delete_authorized(old_att)" in src
    assert src.index("old_att = att_service.read_by_id") < src.index(
        "inv_att_service.delete_by_public_id(link.public_id)"
    )


def test_contract_labor_orphan_purge_authorizes_before_unlink():
    """CL edit path unlinks each BLI's attachment link, then purged the orphan via
    a gated re-read. The Attachment must be read while its link still exists, and
    the purge must go through delete_authorized. Structural pin (the generator is too
    large to drive without a live DB)."""
    import entities.contract_labor.business.bill_service as bill_service_mod

    src = inspect.getsource(bill_service_mod)
    assert "orphan_attachments" in src
    assert "delete_authorized(orphan)" in src
    assert "AttachmentService().delete_by_public_id(public_id=att.public_id)" not in src
    assert src.index("orphan_attachments.setdefault(") < src.index("blia_service.delete_by_public_id(link.public_id)")


# ---------------------------------------------------------------------------
# F2 — a denied COI is omitted from the vendor packet, not a 500
# ---------------------------------------------------------------------------


def test_packet_omits_denied_coi_instead_of_500():
    import entities.vendor_compliance.business.packet_service as packet_mod

    cls = next(v for v in vars(packet_mod).values() if inspect.isclass(v) and hasattr(v, "build_packet"))
    cert = SimpleNamespace(id=3, attachment_id=5)
    denied_att_service = MagicMock()
    denied_att_service.read_by_id.side_effect = EntityNotAccessibleError("Attachment", 5)
    with patch.object(packet_mod, "VendorService") as vendor_svc, patch.object(
        packet_mod, "CertificateOfInsuranceService"
    ) as coi_svc, patch.object(packet_mod, "AttachmentService", return_value=denied_att_service), patch.object(
        packet_mod, "resolve_current_business_license", return_value=None
    ), patch.object(packet_mod, "resolve_current_contractors_license", return_value=None), patch.object(
        packet_mod, "resolve_latest_w9_attachment", return_value=None
    ), patch.object(packet_mod, "merge_pdfs", return_value=b"%PDF") as merge, patch.object(
        cls, "_build_cover_pdf", return_value=b"cover"
    ) as cover:
        vendor_svc.return_value.read_by_public_id.return_value = SimpleNamespace(id=1, name="V", abbreviation=None)
        coi_svc.return_value.read_by_vendor_id.return_value = [cert]
        result = cls().build_packet("vendor-public-id")
    assert result is not None
    rows = cover.call_args[0][1]
    assert not any(r.get("type_label") == "Certificate of Insurance" for r in rows)
    merge.assert_called_once()
    assert merge.call_args[0][0] == []


# ---------------------------------------------------------------------------
# F3 — ILIA routes map a denial to 404 (not a 500 carrying the id)
# ---------------------------------------------------------------------------


def test_ilia_by_public_id_denial_is_404():
    _override(ilia_router.get_invoice_line_item_attachment_by_public_id_router)
    with patch.object(ilia_router.service, "read_by_public_id", side_effect=EntityNotAccessibleError("InvoiceLineItemAttachment", 77)):
        response = TestClient(app, raise_server_exceptions=False).get(f"/api/v1/get/invoice-line-item-attachment/{UUID_A}")
    assert response.status_code == 404
    assert "77" not in response.text


# ---------------------------------------------------------------------------
# (a) the BLIA / ELIA conflict assertions match the constant, not a substring
# ---------------------------------------------------------------------------


def test_conflict_prefix_constant_is_the_message_head():
    assert ATTACHMENT_LINK_CONFLICT_PREFIX.endswith(": ")
    assert "different attachment" in ATTACHMENT_LINK_CONFLICT_PREFIX


# ---------------------------------------------------------------------------
# (b) one denial matrix: 404 and no repo write / storage call
# ---------------------------------------------------------------------------


def _client():
    return TestClient(app, raise_server_exceptions=False)


def _override(handler):
    dep = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"id": ACTOR_ID, "tenant_id": 1}


def _denial_case(name):
    """Returns (method, path, json_body, handler, patches, forbidden_mocks)."""
    deny_att_gate = patch("shared.access._check", return_value=False)
    att_repo = MagicMock()
    att_repo.read_by_public_id.return_value = _att()
    att_repo.read_by_id.return_value = _att()
    att_repo.update_by_id.return_value = _att()
    att_repo.delete_by_id.return_value = _att()
    repo_patch = patch.object(attachment_router.service, "repo", att_repo)
    storage = MagicMock()
    storage_patch = patch.object(attachment_router, "AzureBlobStorage", storage)
    engine = MagicMock()
    engine.return_value.execute_synchronous.return_value = {"success": True, "data": {}}
    cases = {
        "attachment_update": ("PUT", f"/api/v1/update/attachment/{UUID_A}", {"row_version": "AAAA", "description": "x"}, attachment_router.update_attachment_by_public_id_router, [patch.object(attachment_router, "ProcessEngine", engine)], [att_repo.update_by_id, engine]),
        "attachment_archive": ("PUT", f"/api/v1/archive/attachment/{UUID_A}", None, attachment_router.archive_attachment_router, [], [att_repo.update_by_id]),
        "attachment_unarchive": ("PUT", f"/api/v1/unarchive/attachment/{UUID_A}", None, attachment_router.unarchive_attachment_router, [], [att_repo.update_by_id]),
        "attachment_delete": ("DELETE", f"/api/v1/delete/attachment/{UUID_A}", None, attachment_router.delete_attachment_by_public_id_router, [], [att_repo.delete_by_id, storage]),
        "attachment_get_by_public_id": ("GET", f"/api/v1/get/attachment/{UUID_A}", None, attachment_router.get_attachment_by_public_id_router, [], [storage]),
        "attachment_get_by_id": ("GET", "/api/v1/get/attachment/id/5", None, attachment_router.get_attachment_by_id_router, [], [storage]),
    }
    return cases[name], [repo_patch, storage_patch, deny_att_gate]


@pytest.mark.parametrize(
    "case",
    [
        "attachment_update",
        "attachment_archive",
        "attachment_unarchive",
        "attachment_delete",
        "attachment_get_by_public_id",
        "attachment_get_by_id",
    ],
)
def test_attachment_by_id_denial_is_404_no_write(case):
    from contextlib import ExitStack

    (method, path, body, handler, extra, forbidden), patches = _denial_case(case)
    _override(handler)
    with ExitStack() as stack:
        for ctx in (*patches, *extra):
            stack.enter_context(ctx)
        response = _client().request(method, path, json=body)
    assert response.status_code == 404
    for mock in forbidden:
        mock.assert_not_called()


def test_blia_delete_denial_is_404_no_write():
    engine = MagicMock()
    _override(blia_router.delete_bill_line_item_attachment_by_public_id_router)
    with patch.object(blia_router.service, "read_by_public_id", side_effect=EntityNotAccessibleError("BillLineItemAttachment", 1)), patch.object(
        blia_router, "ProcessEngine", engine
    ):
        response = _client().delete(f"/api/v1/delete/bill-line-item-attachment/{UUID_A}")
    assert response.status_code == 404
    engine.assert_not_called()


def test_elia_delete_denial_is_404_no_write():
    engine = MagicMock()
    _override(elia_router.delete_expense_line_item_attachment_by_public_id_router)
    with patch.object(elia_router.service, "read_by_public_id", side_effect=EntityNotAccessibleError("ExpenseLineItemAttachment", 1)), patch.object(
        elia_router, "ProcessEngine", engine
    ):
        response = _client().delete(f"/api/v1/delete/expense-line-item-attachment/{UUID_A}")
    assert response.status_code == 404
    engine.assert_not_called()


def test_blia_create_pre_read_denial_is_404_no_write():
    engine = MagicMock()
    _override(blia_router.create_bill_line_item_attachment_router)
    with patch.object(blia_router, "BillLineItemService", side_effect=EntityNotAccessibleError("BillLineItem", 3)), patch.object(
        blia_router, "ProcessEngine", engine
    ):
        response = _client().post(
            "/api/v1/create/bill-line-item-attachment",
            json={"bill_line_item_public_id": UUID_A, "attachment_public_id": UUID_B},
        )
    assert response.status_code == 404
    engine.assert_not_called()


def test_elia_create_pre_read_denial_is_404_no_write():
    engine = MagicMock()
    _override(elia_router.create_expense_line_item_attachment_router)
    with patch.object(elia_router, "ExpenseLineItemService", side_effect=EntityNotAccessibleError("ExpenseLineItem", 3)), patch.object(
        elia_router, "ProcessEngine", engine
    ):
        response = _client().post(
            "/api/v1/create/expense-line-item-attachment",
            json={"expense_line_item_public_id": UUID_A, "attachment_public_id": UUID_B},
        )
    assert response.status_code == 404
    engine.assert_not_called()
