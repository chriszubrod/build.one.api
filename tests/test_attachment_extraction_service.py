"""U-187 — AttachmentExtractionService: text-layer-first, DI fallback (mocked),
sync-proof VendorInvoiceNumber, idempotent skip; plus the pure invoice-number parser."""

import json
import types

from entities.attachment.business.extraction_service import (
    REDACTION_VERSION,
    AttachmentExtractionService,
    parse_vendor_invoice_number,
    _extract_text_layer,
    _has_usable_text,
)
from tests._attachment_extraction_fakes import _FakeDI, _FakeRepo, _FakeStorage, _text_pdf_bytes


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
        parse_invoice_number=True,
        is_taxpayer_document=False,
        claim_modified_datetime=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# --- text-layer path (no DI) ----------------------------------------------- #


def test_extract_text_layer_path_parses_number_and_never_calls_di():
    pdf = _text_pdf_bytes(["ACME SUPPLY CO", "Invoice # 202980/1", "Total: $3,553.71"])
    assert _has_usable_text(_extract_text_layer(pdf, "application/pdf")[0])

    att = _attachment()
    repo = _FakeRepo()
    storage = _FakeStorage(downloads={att.blob_url: pdf})
    di = _FakeDI(forbid=True)  # asserts DI is not called
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=di)

    result = svc._extract(att)

    assert result["status"] == "completed"

    assert storage.deleted == []  # never deletes a source or published blob
    assert result["method"] == "text_layer"
    assert di.text_calls == []
    assert len(storage.uploads) == 1  # extraction JSON persisted to blob
    assert len(repo.updates) == 1
    call = repo.updates[0]
    assert call["extraction_status"] == "completed"
    assert "202980" in (call["vendor_invoice_number"] or "")


# --- DI fallback path (image-only scan) ------------------------------------ #


def test_extract_di_fallback_for_image_only_scan():
    att = _attachment(content_type="image/png")
    repo = _FakeRepo()
    storage = _FakeStorage(downloads={att.blob_url: b"\x89PNG\r\n\x1a\n not-a-pdf raster bytes"})
    di = _FakeDI(
        text_result={
            "content": "scanned invoice text",
            "pages_count": 1,
            "key_value_pairs": [{"key": "Invoice #", "value": "INV-9", "confidence": None}],
        }
    )
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=di)

    result = svc._extract(att)

    assert result["status"] == "completed"

    assert storage.deleted == []  # never deletes a source or published blob
    assert result["method"] == "document_intelligence_read"
    assert len(di.text_calls) == 1
    assert repo.updates[0]["vendor_invoice_number"] == "INV-9"


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
        downloads={
            prior_blob: json.dumps(
                {"source_file_hash": "hash-abc", "redaction_version": REDACTION_VERSION}
            ).encode("utf-8"),
        },
    )
    di = _FakeDI(forbid=True)
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=di)

    result = svc._extract(att)

    assert result["status"] == "skipped"

    assert storage.deleted == []  # never deletes a source or published blob
    assert result["reason"] == "unchanged"
    assert di.text_calls == []
    assert repo.updates == []  # nothing re-persisted


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
    )
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(forbid=True))

    result = svc._extract(att)

    assert result["status"] == "completed"  # hash differs → re-extracted


# --- extract_pending scoping ----------------------------------------------- #


def test_extract_pending_processes_rows_the_repo_returns(monkeypatch):
    monkeypatch.setenv("ATTACHMENT_EXTRACTION_GROUPS", "bill")
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    pending = _attachment(id=1, category="bill_line_item")

    repo = _FakeRepo(rows=[pending])
    storage = _FakeStorage(downloads={pending.blob_url: pdf})
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(forbid=True))

    out = svc.extract_pending(limit=10)

    assert out["processed"] == 1
    assert out["results"][0]["status"] == "completed"
