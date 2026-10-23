"""U-723 Phase 1: document-text fingerprint normalizer + upload hooks."""
from __future__ import annotations

import io
import inspect
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from app import app
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from entities.attachment.api import router as attachment_router
from entities.attachment.business.text_fingerprint import (
    FINGERPRINT_VERSION,
    fingerprint_document_text,
    fingerprint_pdf_bytes,
    normalize_document_text,
)

# Long enough body to exceed the 200-char normalized floor after drops.
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


_INVOICE_BODY = (
    "wilson electric invoice number inv-8842 project he7 main panel upgrade "
    "materials labor permit fees subtotal tax total due net 30 days "
    "remit to accounts payable vendor address line city state zip "
    "description qty unit price amount line two line three line four "
)


def _build_pdf_bytes(body_text: str, *, creation_date: str | None = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    text_obj = c.beginText(72, 720)
    for line in body_text.split():
        text_obj.textLine(line)
    c.drawText(text_obj)
    c.showPage()
    c.save()
    raw = buf.getvalue()
    if creation_date is None:
        return raw
    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.add_metadata({"/CreationDate": creation_date, "/ModDate": creation_date})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_same_fingerprint_ignores_intuit_link_and_clock_time():
    link = (
        "connect.intuit.com/t/scs-v1-"
        "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcd1234"
    )
    a = f"{_INVOICE_BODY} remit {link} notice 2:45 pm end"
    b = f"{_INVOICE_BODY} remit notice end"
    assert fingerprint_document_text(a) == fingerprint_document_text(b)


def test_different_invoice_date_changes_fingerprint():
    a = f"{_INVOICE_BODY} invoice date 10/15/2026"
    b = f"{_INVOICE_BODY} invoice date 10/16/2026"
    fa, fb = fingerprint_document_text(a), fingerprint_document_text(b)
    assert fa and fb and fa != fb


def test_different_amount_changes_fingerprint():
    a = f"{_INVOICE_BODY} total amount $1,500.00"
    b = f"{_INVOICE_BODY} total amount $1,500.01"
    fa, fb = fingerprint_document_text(a), fingerprint_document_text(b)
    assert fa and fb and fa != fb


def test_short_normalized_text_returns_none():
    assert fingerprint_document_text("too short") is None


def test_tin_masked_does_not_affect_fingerprint():
    ein_a = f"{_INVOICE_BODY} employer id 12-3456789"
    ein_b = f"{_INVOICE_BODY} employer id 98-7656789"
    assert fingerprint_document_text(ein_a) == fingerprint_document_text(ein_b)
    norm = normalize_document_text(ein_a)
    assert "123456789" not in norm.replace("-", "").replace(" ", "")
    assert "3456789" not in norm or "*" in norm


def test_fingerprint_version_constant():
    assert FINGERPRINT_VERSION == "u723-v1"


def test_pdf_same_text_different_metadata_same_fingerprint():
    body = _INVOICE_BODY + " invoice date 03/01/2026 total $2,000.00"
    pdf_a = _build_pdf_bytes(body, creation_date="D:20260101000000")
    pdf_b = _build_pdf_bytes(body, creation_date="D:20261231235959")
    assert fingerprint_pdf_bytes(pdf_a) == fingerprint_pdf_bytes(pdf_b)


def test_fingerprint_pdf_bytes_never_raises_on_garbage():
    assert fingerprint_pdf_bytes(b"not a pdf") is None
    assert fingerprint_pdf_bytes(b"") is None
    assert fingerprint_pdf_bytes(b"%PDF-1.4\n% corrupted") is None


def test_upload_attachment_computes_fingerprint(client, clear_dependency_overrides):
    _rbac_override_for(attachment_router.upload_attachment_router)
    mock_storage_cls = MagicMock()
    mock_storage_cls.return_value.upload_file.return_value = "https://blob/x"

    fake_attachment = MagicMock()
    fake_attachment.id = 42
    fake_attachment.to_dict.return_value = {"public_id": "new-att"}

    with (
        patch.object(attachment_router, "AzureBlobStorage", mock_storage_cls),
        patch.object(attachment_router.service, "read_by_hash", return_value=None),
        patch.object(attachment_router.service, "calculate_hash", return_value="abc123"),
        patch.object(attachment_router.service, "extract_extension", return_value=".pdf"),
        patch.object(attachment_router.service, "build_blob_name", return_value="blob.pdf"),
        patch.object(attachment_router.service, "validate_file_size"),
        patch.object(attachment_router, "_mark_attachment_pending_extraction"),
        patch.object(attachment_router.service, "create", return_value=fake_attachment),
        patch.object(
            attachment_router,
            "persist_text_fingerprint_failure_isolated",
        ) as mock_fp,
    ):
        pdf = _build_pdf_bytes(_INVOICE_BODY + " total $500.00 date 01/02/2026")
        response = client.post(
            "/api/v1/upload/attachment",
            files={"file": ("invoice.pdf", io.BytesIO(pdf), "application/pdf")},
        )

    assert response.status_code == 200
    mock_fp.assert_called_once()
    assert mock_fp.call_args.kwargs["attachment_id"] == 42
    assert mock_fp.call_args.kwargs["content_type"] == "application/pdf"


def test_upload_survives_fingerprint_failure(client, clear_dependency_overrides):
    _rbac_override_for(attachment_router.upload_attachment_router)
    mock_storage_cls = MagicMock()
    mock_storage_cls.return_value.upload_file.return_value = "https://blob/x"

    fake_attachment = MagicMock()
    fake_attachment.id = 7
    fake_attachment.to_dict.return_value = {"public_id": "ok"}

    with (
        patch.object(attachment_router, "AzureBlobStorage", mock_storage_cls),
        patch.object(attachment_router.service, "read_by_hash", return_value=None),
        patch.object(attachment_router.service, "calculate_hash", return_value="abc"),
        patch.object(attachment_router.service, "extract_extension", return_value=".pdf"),
        patch.object(attachment_router.service, "build_blob_name", return_value="b.pdf"),
        patch.object(attachment_router.service, "validate_file_size"),
        patch.object(attachment_router, "_mark_attachment_pending_extraction"),
        patch.object(attachment_router.service, "create", return_value=fake_attachment),
        patch(
            "entities.attachment.business.text_fingerprint_service.AttachmentTextFingerprintService.upsert_from_pdf_bytes",
            side_effect=RuntimeError("fp down"),
        ),
    ):
        pdf = _build_pdf_bytes(_INVOICE_BODY)
        response = client.post(
            "/api/v1/upload/attachment",
            files={"file": ("invoice.pdf", io.BytesIO(pdf), "application/pdf")},
        )

    assert response.status_code == 200


def test_persist_text_fingerprint_failure_isolated_swallows_errors():
    from entities.attachment.business.text_fingerprint_service import (
        persist_text_fingerprint_failure_isolated,
    )

    with patch(
        "entities.attachment.business.text_fingerprint_service.AttachmentTextFingerprintService"
    ) as svc_cls:
        svc_cls.return_value.upsert_from_pdf_bytes.side_effect = RuntimeError("db")
        persist_text_fingerprint_failure_isolated(
            attachment_id=1,
            pdf_bytes=b"%PDF",
            content_type="application/pdf",
        )
