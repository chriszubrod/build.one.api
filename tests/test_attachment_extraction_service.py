"""U-187 — AttachmentExtractionService: text-layer-first, DI fallback (mocked),
sync-proof VendorInvoiceNumber, idempotent skip; plus the pure invoice-number parser."""

import io
import json
import types

import pytest

from entities.attachment.business.extraction_service import (
    AttachmentExtractionService,
    parse_vendor_invoice_number,
    _extract_text_layer,
    _has_usable_text,
)


# --- pure parser ----------------------------------------------------------- #


def test_parse_number_from_kvp_invoice_hash():
    assert parse_vendor_invoice_number(
        [{"key": "Invoice #", "value": "202980/1"}], "unused"
    ) == "202980/1"


def test_parse_number_from_kvp_ref_no():
    assert parse_vendor_invoice_number(
        [{"key": "Ref No", "value": "R-77"}], None
    ) == "R-77"


def test_parse_number_falls_back_to_text():
    assert parse_vendor_invoice_number([], "Invoice No: 4567 dated today") == "4567"


def test_parse_number_none_when_absent():
    assert parse_vendor_invoice_number([], "just some prose with no number label") is None


def test_parse_number_skips_non_numeric_candidate():
    # "Invoice To" must not be mistaken for a number.
    assert parse_vendor_invoice_number([], "Invoice To: Acme") is None


# --- fakes ----------------------------------------------------------------- #


class _FakeRepo:
    def __init__(self):
        self.calls = []

    def update_extraction(self, **kwargs):
        self.calls.append(kwargs)
        return None


class _FakeStorage:
    def __init__(self, downloads=None, download_meta=None):
        self._downloads = downloads or {}
        self._meta = download_meta or {}
        self.uploads = []

    def download_file(self, blob_url):
        if blob_url not in self._downloads:
            raise KeyError(f"no such blob: {blob_url}")
        return self._downloads[blob_url], self._meta.get(blob_url, {})

    def upload_file(self, *, blob_name, file_content, content_type):
        self.uploads.append({"blob_name": blob_name, "content_type": content_type})
        return f"https://blob.example/{blob_name}"


class _FakeDI:
    def __init__(self, result=None, boom=False):
        self.result = result
        self.boom = boom
        self.called = 0

    def extract_invoice(self, content, content_type):
        self.called += 1
        if self.boom:
            raise AssertionError("DI must NOT be called on the text-layer path")
        return self.result


def _attachment(**overrides):
    base = dict(
        id=1,
        public_id="aaaaaaaa-0000-0000-0000-000000000001",
        blob_url="https://blob.example/source.pdf",
        content_type="application/pdf",
        file_hash="hash-abc",
        vendor_invoice_number=None,
        extraction_status="pending",
        extracted_text_blob_url=None,
        category="bill_line_item",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _text_pdf_bytes(lines):
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


# --- text-layer path (no DI) ----------------------------------------------- #


def test_extract_text_layer_path_parses_number_and_never_calls_di():
    pdf = _text_pdf_bytes(["ACME SUPPLY CO", "Invoice # 202980/1", "Total: $3,553.71"])
    assert _has_usable_text(_extract_text_layer(pdf, "application/pdf")[0])

    att = _attachment()
    repo = _FakeRepo()
    storage = _FakeStorage(
        downloads={att.blob_url: pdf},
        download_meta={att.blob_url: {"content_type": "application/pdf"}},
    )
    di = _FakeDI(boom=True)  # asserts DI is not called
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=di)

    result = svc._extract(att)

    assert result["status"] == "completed"
    assert result["method"] == "text_layer"
    assert di.called == 0
    assert len(storage.uploads) == 1  # extraction JSON persisted to blob
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["extraction_status"] == "completed"
    assert "202980" in (call["vendor_invoice_number"] or "")


# --- DI fallback path (image-only scan) ------------------------------------ #


def test_extract_di_fallback_for_image_only_scan():
    att = _attachment(content_type="image/png")
    repo = _FakeRepo()
    storage = _FakeStorage(
        downloads={att.blob_url: b"\x89PNG\r\n\x1a\n not-a-pdf raster bytes"},
        download_meta={att.blob_url: {"content_type": "image/png"}},
    )
    di = _FakeDI(
        result={
            "content": "scanned invoice text",
            "pages_count": 1,
            "key_value_pairs": [{"key": "Invoice #", "value": "INV-9", "confidence": None}],
        }
    )
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=di)

    result = svc._extract(att)

    assert result["status"] == "completed"
    assert result["method"] == "document_intelligence"
    assert di.called == 1
    assert repo.calls[0]["vendor_invoice_number"] == "INV-9"


# --- idempotent skip ------------------------------------------------------- #


def test_extract_skips_when_completed_same_hash():
    prior_blob = "https://blob.example/attachment-extractions/x.json"
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url=prior_blob,
        file_hash="hash-abc",
    )
    repo = _FakeRepo()
    storage = _FakeStorage(
        downloads={prior_blob: json.dumps({"source_file_hash": "hash-abc"}).encode("utf-8")},
    )
    di = _FakeDI(boom=True)
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=di)

    result = svc._extract(att)

    assert result["status"] == "skipped"
    assert result["reason"] == "unchanged"
    assert di.called == 0
    assert repo.calls == []  # nothing re-persisted


def test_extract_reprocesses_when_hash_changed():
    prior_blob = "https://blob.example/attachment-extractions/x.json"
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 555", "Total 10.00"])
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url=prior_blob,
        file_hash="hash-NEW",
    )
    repo = _FakeRepo()
    storage = _FakeStorage(
        downloads={
            prior_blob: json.dumps({"source_file_hash": "hash-OLD"}).encode("utf-8"),
            att.blob_url: pdf,
        },
        download_meta={att.blob_url: {"content_type": "application/pdf"}},
    )
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(boom=True))

    result = svc._extract(att)

    assert result["status"] == "completed"  # hash differs → re-extracted


# --- extract_pending scoping ----------------------------------------------- #


def test_extract_pending_scopes_to_pending_status_and_limit():
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    pending = _attachment(id=1, category="bill_line_item")
    completed = _attachment(id=2, extraction_status="completed", category="bill_line_item")
    null_status = _attachment(id=3, extraction_status=None, category="bill_line_item")

    repo = _FakeRepo()
    repo.read_pending_extraction = lambda: [pending, completed, null_status]
    storage = _FakeStorage(
        downloads={pending.blob_url: pdf},
        download_meta={pending.blob_url: {"content_type": "application/pdf"}},
    )
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(boom=True))

    out = svc.extract_pending(limit=10)

    # Only the explicitly-'pending' row is processed (completed + NULL skipped).
    assert out["pending_in_scope"] == 1
    assert out["processed"] == 1
    assert out["results"][0]["status"] == "completed"
