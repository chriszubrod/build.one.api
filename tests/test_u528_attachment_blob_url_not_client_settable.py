"""U-528 — client-supplied blob_url must not be settable on attachment HTTP routes.

Arbitrary blob read-to-exfil via review-notification send_mail was reachable
through POST /create/attachment and PUT /update/attachment forwarding blob_url
from the request body. Upload and bill-completion rename keep server-derived URLs.
"""

import ast
import inspect
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import entities.attachment.api.router as attachment_router
from app import app
from core.workflow.api.process_engine import ProcessEngine
from entities.attachment.api.schemas import AttachmentUpdate
from tests.conftest import REPO_ROOT

CREATE_PATH = "/api/v1/create/attachment"
UPDATE_PATH = "/api/v1/update/attachment/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UPLOAD_PATH = "/api/v1/upload/attachment"
EVIL_BLOB_URL = "https://evil.example/container/secret-salary-data.pdf"
SERVER_BLOB_URL = (
    "https://buildone.blob.core.windows.net/attachments/"
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.pdf"
)


def _mounted_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def _rbac_override_for(handler):
    rbac_dependency = inspect.signature(handler).parameters[
        "current_user"
    ].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {
        "id": 17,
        "tenant_id": 1,
    }


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# (a) Regression guard — create route gone; update must not forward blob_url
# ---------------------------------------------------------------------------


def test_create_attachment_route_is_absent_from_app():
    assert CREATE_PATH not in _mounted_paths()


def test_post_create_attachment_returns_404_not_405(client, clear_dependency_overrides):
    # No RBAC override: Starlette 404s on no route match before dependencies run.
    response = client.post(
        CREATE_PATH,
        json={
            "filename": "x.pdf",
            "blob_url": EVIL_BLOB_URL,
        },
    )
    assert response.status_code == 404


def test_update_attachment_does_not_forward_client_blob_url(
    client, clear_dependency_overrides
):
    _rbac_override_for(attachment_router.update_attachment_by_public_id_router)
    with patch.object(
        ProcessEngine,
        "execute_synchronous",
        return_value={"success": True, "data": {}},
    ) as mock_exec:
        response = client.put(
            UPDATE_PATH,
            json={
                "blob_url": EVIL_BLOB_URL,
                "description": "innocent metadata edit",
            },
        )
    assert response.status_code == 200
    payload = mock_exec.call_args[0][0].payload
    assert "blob_url" not in payload


# ---------------------------------------------------------------------------
# (b) Schemas — blob_url not client-reachable
# ---------------------------------------------------------------------------


def test_attachment_update_schema_does_not_declare_blob_url():
    assert "blob_url" not in AttachmentUpdate.model_fields


def test_attachment_create_schema_removed():
    import entities.attachment.api.schemas as attachment_schemas

    assert not hasattr(attachment_schemas, "AttachmentCreate")


# ---------------------------------------------------------------------------
# (c) Upload still stores server-derived blob_url on create
# ---------------------------------------------------------------------------


def test_upload_attachment_passes_storage_derived_blob_url_to_service_create(
    client, clear_dependency_overrides
):
    _rbac_override_for(attachment_router.upload_attachment_router)
    mock_storage_cls = MagicMock()
    mock_storage_cls.return_value.upload_file.return_value = SERVER_BLOB_URL

    fake_attachment = MagicMock()
    fake_attachment.to_dict.return_value = {"public_id": "new-att"}

    with (
        patch.object(attachment_router, "AzureBlobStorage", mock_storage_cls),
        patch.object(attachment_router.service, "read_by_hash", return_value=None),
        patch.object(attachment_router.service, "calculate_hash", return_value="abc123"),
        patch.object(attachment_router.service, "extract_extension", return_value=".pdf"),
        patch.object(
            attachment_router.service, "build_blob_name", return_value="blob.pdf"
        ),
        patch.object(attachment_router.service, "validate_file_size"),
        patch.object(
            attachment_router,
            "_mark_attachment_pending_extraction",
        ),
        patch.object(
            attachment_router.service,
            "create",
            autospec=True,
            return_value=fake_attachment,
        ) as mock_create,
    ):
        response = client.post(
            UPLOAD_PATH,
            files={"file": ("invoice.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 200
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["blob_url"] == SERVER_BLOB_URL
    assert mock_create.call_args.kwargs["blob_url"] != EVIL_BLOB_URL


# ---------------------------------------------------------------------------
# (d) Bill completion blob rename still updates blob_url via service
# ---------------------------------------------------------------------------


def test_rename_invoice_blob_on_complete_still_passes_blob_url_to_update():
    """Pinned at the call site so a naive service-layer cleanup cannot drop it."""
    tree = ast.parse(
        (REPO_ROOT / "entities/bill/business/service.py").read_text(encoding="utf-8")
    )
    renames = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update_by_public_id"
        and any(kw.arg == "original_filename" for kw in node.keywords)
    ]
    assert len(renames) == 1, f"expected one blob-rename call, found {len(renames)}"
    rename_call = renames[0]
    blob_url_kw = [kw for kw in rename_call.keywords if kw.arg == "blob_url"]
    assert len(blob_url_kw) == 1, "completion rename must pass blob_url="
    assert isinstance(blob_url_kw[0].value, ast.Name), (
        "blob_url must be the storage upload result, not a literal"
    )
    assert blob_url_kw[0].value.id == "new_url"
