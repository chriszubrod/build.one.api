"""U-535a — grouped, link-scoped, claim-based attachment text extraction.

No database and no Azure in this module. Procedure behaviour runs against a real SQL
Server in tests/test_u535a_sql_integration.py; here the procedure text is pinned
structurally and the service, adapters and admin endpoint are exercised with fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import importlib
import inspect
import io
import json
import logging
import re
import threading
import traceback
import types
import warnings
from pathlib import Path

import httpx
import pypdf
import pytest

from entities.attachment.business import extraction_service as es
from entities.attachment.persistence import repo as repo_mod
from integrations.azure.document_intelligence.business import service as di_service
from integrations.azure.document_intelligence.external import client as di_client
from integrations.azure.document_intelligence.external.client import (
    DocumentIntelligenceConfigError,
    DocumentIntelligenceError,
)
from shared import storage as storage_mod
from tests._attachment_extraction_fakes import (
    _FakeDI,
    _FakeRepo,
    _FakeStorage,
    _image_only_pdf_bytes,
    _text_pdf_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ADD_FIELDS_SQL = REPO_ROOT / "entities" / "attachment" / "sql" / "add_extraction_fields.sql"
ENV = "ATTACHMENT_EXTRACTION_GROUPS"
SENTINEL = "999-88-7777"
GROUP_LINK_TABLES = (
    "BillLineItemAttachment",
    "BillCreditLineItemAttachment",
    "ExpenseLineItemAttachment",
    "TaxpayerAttachment",
    "CertificateOfInsurance",
    "BusinessLicenseAttachment",
    "ContractorsLicenseAttachment",
)
ALL_GROUPS = ("bill", "bill_credit", "expense", "w9", "coi", "license")
TICK_FIELDS = {"attachment_id", "status", "method", "pages_count", "reason"}
ERROR_CODE_RE = re.compile(
    r"(no_blob_url|blob_download_error|di_config_error|di_error|persist_error|unexpected)"
    r"(:[A-Za-z]+)?"
)

# Verbatim copy of ReadAttachmentsPendingExtraction as it stands at HEAD. The deployed
# code calls this no-arg procedure, so it must not change under U-535a.
HEAD_READ_PENDING_PROC = """
CREATE OR ALTER PROCEDURE ReadAttachmentsPendingExtraction
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [ExtractionStatus] = 'pending' OR [ExtractionStatus] IS NULL
    ORDER BY [CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
"""


# --- fakes: see tests/_attachment_extraction_fakes.py ---------------------- #


CLAIM_TOKEN = dt.datetime(2026, 1, 1, 12, 0, 0, 123000)


def _att(id, *, content_type="image/png", extraction_status="processing", parse_invoice_number=False, **overrides):
    base = dict(
        id=id,
        public_id=f"aaaaaaaa-0000-0000-0000-{id:012d}",
        blob_url=f"https://blob.example/{id}.bin",
        content_type=content_type,
        file_hash=f"hash-{id}",
        vendor_invoice_number=None,
        extraction_status=extraction_status,
        extracted_text_blob_url=None,
        category=None,
        description=None,
        parse_invoice_number=parse_invoice_number,
        is_taxpayer_document=False,
        claim_modified_datetime=CLAIM_TOKEN,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


IMAGE_BYTES = b"\x89PNG\r\n\x1a\n not-a-pdf raster bytes"
DI_TEXT = {"content": "scanned vendor invoice text", "pages_count": 2, "key_value_pairs": []}


def _service(repo, storage=None, di=None):
    return es.AttachmentExtractionService(
        repo=repo,
        storage=storage or _FakeStorage(
            downloads={f"https://blob.example/{i}.bin": IMAGE_BYTES for i in range(1, 50)}
        ),
        di=di or _FakeDI(text_result=DI_TEXT),
    )


def _malformed_pdf_with_sentinel() -> bytes:
    """A structurally valid PDF whose MediaBox holds a non-numeric token containing the sentinel.

    pypdf logs the token itself (`NumberObject(b'...') invalid`), so this input is what the
    import-time silencing has to hold against.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {SENTINEL} 792] /Contents 4 0 R >>".encode(),
    ]
    content = b"BT /F1 12 Tf 72 700 Td (Hello) Tj ET"
    objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


# --- a. gating: no enabled group → no claim ------------------------------- #


@pytest.mark.parametrize("raw", [None, "", "   ", "nonsense, other"])
def test_no_enabled_group_returns_empty_and_never_claims(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, raw)
    repo = _FakeRepo(rows=[_att(1)])

    out = _service(repo).extract_pending(limit=20)

    assert out == {"groups": [], "processed": 0, "results": []}
    assert repo.claim_calls == []


# --- b. setting normalisation ---------------------------------------------- #


def test_parse_groups_normalises_case_and_whitespace():
    assert es.parse_extraction_groups("BillCredit , bill") == ["bill_credit", "bill"]


def test_parse_groups_drops_unknown_names_with_warning(caplog):
    with caplog.at_level("WARNING"):
        assert es.parse_extraction_groups("bill, bogus,,W9") == ["bill", "w9"]
    assert "bogus" in caplog.text


def test_parse_groups_dedupes_and_handles_none():
    assert es.parse_extraction_groups("bill,BILL, bill") == ["bill"]
    assert es.parse_extraction_groups(None) == []


# --- c. extract_pending contract ------------------------------------------- #


def test_extract_pending_claims_one_row_at_a_time_with_groups_and_lease(monkeypatch):
    monkeypatch.setenv(ENV, "BillCredit , bill")
    repo = _FakeRepo(rows=[_att(1), _att(2)])

    out = _service(repo).extract_pending(limit=7)

    single_claim = {"groups": ["bill_credit", "bill"], "limit": 1, "lease_minutes": 15}
    assert repo.claim_calls == [single_claim] * 3
    assert out["groups"] == ["bill_credit", "bill"]
    assert type(out["processed"]) is int
    assert out["processed"] == 2
    assert [r["status"] for r in out["results"]] == ["completed", "completed"]


def test_extract_pending_never_claims_past_its_limit(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    repo = _FakeRepo(rows=[_att(i) for i in range(1, 6)])

    out = _service(repo).extract_pending(limit=2)

    assert len(repo.claim_calls) == 2
    assert out["processed"] == 2
    assert len(repo.rows) == 3


def test_tick_result_rows_carry_only_fixed_fields(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    pdf = _text_pdf_bytes(["ACME SUPPLY CO", "Invoice Number: 12-3456789", "Total: 10.00"])
    att = _att(1, content_type="application/pdf", parse_invoice_number=True)
    repo = _FakeRepo(rows=[att])
    storage = _FakeStorage(downloads={att.blob_url: pdf})

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True)).extract_pending(limit=5)

    row = out["results"][0]
    assert row["status"] == "completed"
    assert set(row) <= TICK_FIELDS
    assert "vendor_invoice_number" not in row


# --- d. text-layer PDF: no DI ---------------------------------------------- #


def test_text_layer_pdf_completes_without_di(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    pdf = _text_pdf_bytes(["ACME SUPPLY CO", "Invoice # 202980/1", "Total: $3,553.71"])
    att = _att(1, content_type="application/pdf")
    repo = _FakeRepo(rows=[att])
    storage = _FakeStorage(downloads={att.blob_url: pdf})
    di = _FakeDI(forbid=True)

    out = _service(repo, storage=storage, di=di).extract_pending(limit=5)

    assert out["results"][0]["status"] == "completed"
    assert out["results"][0]["method"] == "text_layer"
    assert di.text_calls == []
    assert di.invoice_calls == []


# --- e. image-only inputs: DI read model ----------------------------------- #


def test_image_only_pdf_falls_through_to_di_read(monkeypatch):
    monkeypatch.setenv(ENV, "w9")
    pdf = _image_only_pdf_bytes()
    resources = pypdf.PdfReader(io.BytesIO(pdf)).pages[0]["/Resources"]
    subtypes = {x.get_object()["/Subtype"] for x in resources["/XObject"].values()}
    assert subtypes == {"/Image"}
    assert not es._has_usable_text(es._extract_text_layer(pdf, "application/pdf")[0])
    att = _att(1, content_type="application/pdf")
    repo = _FakeRepo(rows=[att])
    storage = _FakeStorage(downloads={att.blob_url: pdf})
    di = _FakeDI(text_result=DI_TEXT)

    out = _service(repo, storage=storage, di=di).extract_pending(limit=5)

    assert out["results"][0]["method"] == "document_intelligence_read"
    assert di.text_calls == ["application/pdf"]
    assert di.invoice_calls == []


def test_png_image_routes_to_di_read_and_records_method(monkeypatch):
    monkeypatch.setenv(ENV, "w9")
    att = _att(1, content_type="image/png")
    repo = _FakeRepo(rows=[att])
    storage = _FakeStorage(downloads={att.blob_url: IMAGE_BYTES})
    di = _FakeDI(text_result=DI_TEXT)

    out = _service(repo, storage=storage, di=di).extract_pending(limit=5)

    result = out["results"][0]
    assert result["status"] == "completed"
    assert result["method"] == "document_intelligence_read"
    assert result["pages_count"] == 2
    assert di.text_calls == ["image/png"]
    payload = json.loads(storage.uploads[0]["file_content"])
    assert payload["method"] == "document_intelligence_read"


# --- f. DI service model selection ----------------------------------------- #


def _effective_args(args, kwargs):
    bound = inspect.signature(di_client.analyze_document_bytes).bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments


def _recording_analyze(monkeypatch, result):
    calls = []

    def fake(*args, **kwargs):
        calls.append(_effective_args(args, kwargs))
        return result

    monkeypatch.setattr(di_service, "analyze_document_bytes", fake)
    return calls


def test_extract_text_uses_prebuilt_read_without_features(monkeypatch):
    calls = _recording_analyze(
        monkeypatch,
        {"content": "hello", "pages": [{}, {}], "keyValuePairs": [{"key": {"content": "x"}}]},
    )

    out = di_service.DocumentIntelligenceService().extract_text(b"%PDF-1.4", "application/pdf")

    assert calls == [
        {
            "content": b"%PDF-1.4",
            "content_type": "application/pdf",
            "model_id": "prebuilt-read",
            "features": None,
        }
    ]
    assert out == {"content": "hello", "pages_count": 2, "key_value_pairs": []}


def test_extract_invoice_keeps_layout_defaults(monkeypatch):
    calls = _recording_analyze(monkeypatch, {"content": "x", "pages": [{}]})

    di_service.DocumentIntelligenceService().extract_invoice(b"%PDF-1.4", "application/pdf")

    assert calls[0]["model_id"] == "prebuilt-layout"
    assert calls[0]["features"] == "keyValuePairs"


def test_features_none_omits_features_query_param(monkeypatch):
    urls = []

    def fake_request(method, url, **kwargs):
        urls.append(url)
        return types.SimpleNamespace(status_code=202, headers={"Operation-Location": "https://op"}, text="")

    monkeypatch.setattr(di_client, "_settings", lambda: ("https://di.example", "k", "2024-11-30"))
    monkeypatch.setattr(di_client, "_do_request", fake_request)
    monkeypatch.setattr(di_client, "_await_result", lambda op, key: {"content": "ok", "pages": []})

    di_client.analyze_document_bytes(b"%PDF", "application/pdf", model_id="prebuilt-read", features=None)
    di_client.analyze_document_bytes(b"%PDF", "application/pdf")

    assert "features=" not in urls[0]
    assert "prebuilt-read:analyze" in urls[0]
    assert "features=keyValuePairs" in urls[1]


# --- g. W-9 TIN gating (item 1) -------------------------------------------- #

W9_LINES = [
    "INTERNAL REVENUE SERVICE  FORM W-9",
    "Name: Jane Taxpayer",
    "Invoice Number: 12-3456789",
]


def test_w9_text_is_never_parsed_for_an_invoice_number(monkeypatch):
    monkeypatch.setenv(ENV, "w9")
    pdf = _text_pdf_bytes(W9_LINES)
    att = _att(1, content_type="application/pdf", parse_invoice_number=False, is_taxpayer_document=True)
    repo = _FakeRepo(rows=[att])
    storage = _FakeStorage(downloads={att.blob_url: pdf})

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True)).extract_pending(limit=5)

    assert repo.updates[0]["extraction_status"] == "completed"
    assert repo.updates[0]["vendor_invoice_number"] is None
    assert "12-3456789" not in json.dumps(out)
    assert all("vendor_invoice_number" not in r for r in out["results"])


def test_bill_attachment_still_parses_invoice_number(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    pdf = _text_pdf_bytes(["ACME SUPPLY CO", "Invoice Number: 12-3456789", "Total: 10.00"])
    att = _att(1, content_type="application/pdf", parse_invoice_number=True)
    repo = _FakeRepo(rows=[att])
    storage = _FakeStorage(downloads={att.blob_url: pdf})

    _service(repo, storage=storage, di=_FakeDI(forbid=True)).extract_pending(limit=5)

    assert repo.updates[0]["vendor_invoice_number"] == "12-3456789"


# --- h. no document bytes or exception text leak (item 2) ------------------ #


def _log_text(caplog) -> str:
    parts = []
    for rec in caplog.records:
        parts.append(rec.getMessage())
        parts.append(repr(rec.args))
        parts.append(rec.exc_text or "")
        if rec.exc_info:
            parts.append("".join(traceback.format_exception(*rec.exc_info)))
    return "\n".join(parts)


SENTINEL_CASES = [
    "no_blob_url",
    "blob_download_error",
    "di_config_error",
    "di_error",
    "persist_error",
    "unexpected",
]


@pytest.mark.parametrize("case", SENTINEL_CASES)
def test_sentinel_never_reaches_logs_error_column_or_tick_response(case, monkeypatch, caplog):
    monkeypatch.setenv(ENV, "bill")
    att = _att(1, parse_invoice_number=True, description=SENTINEL)
    downloads = {att.blob_url: IMAGE_BYTES}
    di_result = DI_TEXT
    storage_kwargs = {}

    if case == "no_blob_url":
        att = _att(1, blob_url=None, description=SENTINEL)
        expected = ("no_blob_url", "no_blob_url")
    elif case == "blob_download_error":
        downloads = {att.blob_url: RuntimeError(f"blob gone {SENTINEL}")}
        expected = ("blob_download_error", "blob_download_error:RuntimeError")
    elif case == "di_config_error":
        di_result = DocumentIntelligenceConfigError(f"no key {SENTINEL}")
        expected = ("di_config_error", "di_config_error:DocumentIntelligenceConfigError")
    elif case == "di_error":
        di_result = DocumentIntelligenceError(f"analyze failed {SENTINEL}")
        expected = ("di_error", "di_error:DocumentIntelligenceError")
    elif case == "persist_error":
        storage_kwargs = {"upload_error": OSError(f"write failed {SENTINEL}")}
        expected = ("persist_error", "persist_error:OSError")
    elif case == "unexpected":
        di_result = ValueError(f"surprise {SENTINEL}")
        expected = ("unexpected", "unexpected:ValueError")

    repo = _FakeRepo(rows=[att])
    storage = _FakeStorage(downloads=downloads, **storage_kwargs)
    svc = _service(repo, storage=storage, di=_FakeDI(text_result=di_result))
    caplog.set_level(logging.DEBUG)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = svc.extract_pending(limit=5)

    expected_reason, expected_error = expected
    assert [r["reason"] for r in out["results"]] == [expected_reason]
    assert repo.updates[-1]["extraction_status"] == "failed"
    assert repo.updates[-1]["extraction_error"] == expected_error
    assert ERROR_CODE_RE.fullmatch(repo.updates[-1]["extraction_error"])

    assert SENTINEL not in _log_text(caplog)
    assert SENTINEL not in json.dumps(out)
    assert all(SENTINEL not in json.dumps(u, default=str) for u in repo.updates)
    assert not [str(w.message) for w in caught if SENTINEL in str(w.message)]
    assert set().union(*(set(r) for r in out["results"])) <= TICK_FIELDS


@pytest.fixture
def import_time_silencing():
    """Apply shared observability pypdf silencing (U-535d) in a warnings scope pytest won't discard."""
    from shared.observability import silence_pypdf_loggers

    with warnings.catch_warnings():
        silence_pypdf_loggers()
        yield


@pytest.mark.usefixtures("import_time_silencing")
def test_pypdf_is_silenced_for_the_whole_process():
    assert logging.getLogger("pypdf").level > logging.CRITICAL
    assert any(
        action == "ignore" and module is not None and module.pattern == r"pypdf(\..*)?"
        for action, _message, _category, module, _lineno in warnings.filters
    )


@pytest.mark.usefixtures("import_time_silencing")
def test_real_malformed_pdf_never_leaks_its_bytes_across_overlapping_threads(monkeypatch, caplog):
    monkeypatch.setenv(ENV, "bill")
    caplog.set_level(logging.DEBUG)
    pdf = _malformed_pdf_with_sentinel()
    barrier = threading.Barrier(2)
    outcomes: list[dict] = []
    errors: list[BaseException] = []

    def run(attachment_id):
        try:
            att = _att(attachment_id, content_type="application/pdf", parse_invoice_number=True)
            svc = _service(
                _FakeRepo(),
                storage=_FakeStorage(downloads={att.blob_url: pdf}),
                di=_FakeDI(text_result=DI_TEXT),
            )
            barrier.wait()
            outcomes.append(svc._extract(att))
        except BaseException as e:  # surfaced below; a thread must not die silently
            errors.append(e)

    with warnings.catch_warnings(record=True) as caught:
        threads = [threading.Thread(target=run, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert errors == []
    assert [o["status"] for o in outcomes] == ["completed", "completed"]
    assert SENTINEL not in _log_text(caplog)
    assert not [str(w.message) for w in caught if SENTINEL in str(w.message)]


# --- i. claim isolation (item 3) and per-row isolation --------------------- #


def test_one_failing_row_does_not_stop_the_others(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    bad = _att(1, content_type="application/pdf")
    good = _att(2, content_type="application/pdf")
    repo = _FakeRepo(rows=[bad, good])
    storage = _FakeStorage(downloads={bad.blob_url: RuntimeError("gone"), good.blob_url: pdf})

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True)).extract_pending(limit=5)

    assert [r["status"] for r in out["results"]] == ["failed", "completed"]
    terminal = {}
    for update in repo.updates:
        terminal[update["id"]] = update["extraction_status"]
    assert terminal == {1: "failed", 2: "completed"}


def test_terminal_write_failure_is_reported_and_no_failed_write_is_attempted(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    att = _att(1, content_type="application/pdf")
    repo = _FakeRepo(rows=[att], fail_terminal=True)
    storage = _FakeStorage(downloads={att.blob_url: pdf})

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True)).extract_pending(limit=5)

    assert out["results"] == [{"status": "failed", "reason": "terminal_write_failed", "attachment_id": 1}]
    assert repo.updates == []


def test_lost_claim_on_completion_is_skipped_and_guarded_by_the_token(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    att = _att(1, content_type="application/pdf")
    repo = _FakeRepo(rows=[att], lose_claim=True)
    storage = _FakeStorage(downloads={att.blob_url: pdf})

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True)).extract_pending(limit=5)

    assert out["results"] == [{"status": "skipped", "reason": "claim_lost", "attachment_id": 1}]
    assert repo.updates[0]["extraction_status"] == "completed"
    assert repo.updates[0]["expected_status"] == "processing"
    assert repo.updates[0]["expected_modified_datetime"] == CLAIM_TOKEN


def test_lost_claim_on_failure_is_skipped(monkeypatch):
    monkeypatch.setenv(ENV, "bill")
    bad = _att(1, content_type="application/pdf")
    repo = _FakeRepo(rows=[bad], lose_claim=True)
    storage = _FakeStorage(downloads={bad.blob_url: RuntimeError("gone")})

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True)).extract_pending(limit=5)

    assert out["results"] == [{"status": "skipped", "reason": "claim_lost", "attachment_id": 1}]
    assert repo.updates[0]["extraction_status"] == "failed"


# --- j. repo call contract ------------------------------------------------- #


class _FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _patch_repo_db(monkeypatch, cursor):
    calls = []

    @contextlib.contextmanager
    def fake_connection():
        yield _FakeConn(cursor)

    def fake_call(cursor, name, params):
        calls.append({"name": name, "params": params})
        return cursor

    monkeypatch.setattr(repo_mod, "get_connection", fake_connection)
    monkeypatch.setattr(repo_mod, "call_procedure", fake_call)
    return calls


def _db_row(**overrides):
    base = dict(
        Id=1,
        PublicId="aaaaaaaa-0000-0000-0000-000000000001",
        RowVersion=b"\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-01 00:00:00",
        Filename="f.pdf",
        OriginalFilename="f.pdf",
        FileExtension="pdf",
        ContentType="application/pdf",
        FileSize=10,
        FileHash="h",
        BlobUrl="https://blob.example/f",
        Description=None,
        Category=None,
        Tags=None,
        IsArchived=False,
        Status=None,
        DownloadCount=0,
        LastDownloadedDatetime=None,
        ExpirationDate=None,
        StorageTier=None,
        ExtractionStatus="processing",
        ExtractedTextBlobUrl=None,
        ExtractionError=None,
        ExtractedDatetime=None,
        VendorInvoiceNumber=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_repo_claim_passes_groups_limit_and_lease(monkeypatch):
    calls = _patch_repo_db(monkeypatch, _FakeCursor(rows=[]))

    repo_mod.AttachmentRepository().claim_for_extraction(groups=["bill", "w9"], limit=5, lease_minutes=15)

    assert calls == [
        {
            "name": "ClaimAttachmentsForExtraction",
            "params": {"Groups": "bill,w9", "Limit": 5, "LeaseMinutes": 15},
        }
    ]


def test_repo_claim_maps_parse_flag_and_defaults_false(monkeypatch):
    rows = [
        _db_row(ParseInvoiceNumber=1),
        _db_row(Id=2, ParseInvoiceNumber=0),
        _db_row(Id=3),
    ]
    _patch_repo_db(monkeypatch, _FakeCursor(rows=rows))

    claimed = repo_mod.AttachmentRepository().claim_for_extraction(groups=["bill"], limit=5, lease_minutes=15)

    assert [a.parse_invoice_number for a in claimed] == [True, False, False]
    assert all(a.extraction_status == "processing" for a in claimed)


def test_repo_claim_maps_is_taxpayer_document(monkeypatch):
    rows = [
        _db_row(IsTaxpayerDocument=1),
        _db_row(Id=2, IsTaxpayerDocument=0),
        _db_row(Id=3),
    ]
    _patch_repo_db(monkeypatch, _FakeCursor(rows=rows))

    claimed = repo_mod.AttachmentRepository().claim_for_extraction(groups=["w9"], limit=5, lease_minutes=15)

    assert [a.is_taxpayer_document for a in claimed] == [True, False, None]


class _BatchCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _BatchConn:
    def __init__(self, rows, events):
        self._rows = list(rows)
        self._events = events

    def cursor(self):
        return _BatchCursor(self._rows.pop(0))

    def commit(self):
        self._events.append("commit")


def _count_row(*, affected, group="bill", never_extracted_pdfs=3, failed_pdfs=1, already_pending=0, completed=5):
    return types.SimpleNamespace(
        affected=affected,
        group=group,
        never_extracted_pdfs=never_extracted_pdfs,
        failed_pdfs=failed_pdfs,
        already_pending=already_pending,
        completed=completed,
    )


def _patch_batched(monkeypatch, conn, events):
    calls = []

    @contextlib.contextmanager
    def fake_connection():
        yield conn

    def fake_call(cursor, name, params):
        calls.append({"name": name, "params": params})
        events.append("call")
        return cursor

    monkeypatch.setattr(repo_mod, "get_connection", fake_connection)
    monkeypatch.setattr(repo_mod, "call_procedure", fake_call)
    return calls


def test_repo_mark_commits_once_per_batch_until_affected_is_zero(monkeypatch):
    events = []
    conn = _BatchConn(
        [_count_row(affected=500), _count_row(affected=500), _count_row(affected=37), _count_row(affected=0)],
        events,
    )
    calls = _patch_batched(monkeypatch, conn, events)

    out = repo_mod.AttachmentRepository().mark_pending_extraction_by_group(
        group="bill", include_failed=False, batch_size=500, dry_run=False
    )

    assert events == ["call", "commit"] * 4
    assert [c["params"]["DryRun"] for c in calls] == [False] * 4
    assert out["affected"] == 1037
    assert out["group"] == "bill"


def test_repo_mark_dry_run_is_a_single_call_and_writes_nothing(monkeypatch):
    events = []
    conn = _BatchConn([_count_row(affected=0, never_extracted_pdfs=9)], events)
    calls = _patch_batched(monkeypatch, conn, events)

    out = repo_mod.AttachmentRepository().mark_pending_extraction_by_group(
        group="bill", include_failed=True, batch_size=500, dry_run=True
    )

    assert len(calls) == 1
    assert calls[0]["params"] == {"Group": "bill", "IncludeFailed": True, "BatchSize": 500, "DryRun": True}
    assert events == ["call", "commit"]
    assert out["affected"] == 0
    assert out["never_extracted_pdfs"] == 9


# --- k. SQL contracts (structural, mutation-resistant) --------------------- #


def _sql_batches(sql: str) -> list[str]:
    batches, cur = [], []
    for line in sql.splitlines():
        if line.strip().upper() == "GO":
            batches.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    if "\n".join(cur).strip():
        batches.append("\n".join(cur))
    return batches


def _proc_batch(name: str) -> str:
    sql = ADD_FIELDS_SQL.read_text(encoding="utf-8")
    create = rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+{name}\b"
    matches = [b for b in _sql_batches(sql) if re.search(create, b, re.IGNORECASE)]
    assert len(matches) == 1, f"{name}: expected exactly one batch, got {len(matches)}"
    return matches[0][re.search(create, matches[0], re.IGNORECASE).start():]


def sq(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _norm(s: str) -> str:
    return " ".join(s.split())


def _header(body: str) -> str:
    return re.split(r"^\s*AS\s*$", body, maxsplit=1, flags=re.M)[0]


def _between(body: str, start: str, end: str) -> str:
    assert start in body, f"missing {start!r}"
    s = body.index(start)
    assert end in body[s:], f"missing {end!r} after {start!r}"
    return body[s: body.index(end, s)]


CLAIM = "ClaimAttachmentsForExtraction"
MARK = "MarkAttachmentsPendingExtractionByGroup"
UPD = "UpdateAttachmentExtraction"

CLAIM_STATUS_CLAUSE = (
    "(a.[ExtractionStatus] = 'pending' OR (a.[ExtractionStatus] = 'processing' "
    "AND a.[ModifiedDatetime] < DATEADD(MINUTE, -@LeaseMinutes, SYSUTCDATETIME())))"
)
CLAIM_PDF_CLAUSE = "AND (LOWER(a.[ContentType]) = 'application/pdf' OR LOWER(a.[FileExtension]) IN ('pdf', '.pdf'))"


def check_claim_header(body):
    header = sq(_header(body))
    for frag in ("@Groups NVARCHAR(200) = NULL", "@Limit INT = 1", "@LeaseMinutes INT = 15"):
        assert sq(frag) in header, frag


def check_claim_groups(body):
    assert sq("STRING_SPLIT(ISNULL(@Groups, ''), ',')") in sq(body)
    assert sq("WHERE LTRIM(RTRIM([value])) <> ''") in sq(body)


LOCK_BLOCK_START = "WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)"
LOCK_BLOCK_END = "UPDATE dbo.[Attachment]"


def check_claim_eligibility(body):
    block = sq(_between(body, LOCK_BLOCK_START, LOCK_BLOCK_END))
    assert sq(CLAIM_STATUS_CLAUSE) in block, "status/lease eligibility clause"
    assert sq(CLAIM_PDF_CLAUSE) in block, "PDF clause must be ANDed"
    assert "'completed'" not in _between(body, LOCK_BLOCK_START, LOCK_BLOCK_END).lower()
    assert "1 = 1" not in body
    for g in ALL_GROUPS:
        assert sq(f"FROM @Enabled WHERE [Grp] = '{g}')") in block, g
    for t in GROUP_LINK_TABLES:
        assert sq(f"FROM dbo.[{t}] AS x WHERE x.[AttachmentId] = a.[Id]") in block, t


def check_claim_candidates_snapshot(body):
    assert sq("WHERE a.[ExtractionStatus] IN ('pending', 'processing')") in sq(body)
    snapshot = _between(body, "INSERT INTO @Candidates", "DECLARE @Claimed")
    assert "UPDLOCK" not in snapshot and "READPAST" not in snapshot


def check_claim_lock(body):
    assert sq(f"FROM dbo.[Attachment] AS a {LOCK_BLOCK_START}") in sq(body)
    assert "ROWLOCK" not in body


def check_claim_order(body):
    assert sq("ROW_NUMBER() OVER (ORDER BY a.[CreatedDatetime] ASC, a.[Id] ASC)") in sq(body)
    assert sq("ORDER BY a.[CreatedDatetime] ASC, a.[Id] ASC") in sq(body[body.index("SELECT\n        a.[Id]") :])
    assert sq("WHILE @Taken < @Want") in sq(body)


def check_claim_update(body):
    upd = sq(_between(body, LOCK_BLOCK_END, "WHERE [Id] = @Id"))
    assert sq("SET [ExtractionStatus] = 'processing'") in upd
    assert sq("[ModifiedDatetime] = SYSUTCDATETIME()") in upd


def check_claim_token(body):
    assert sq("a.[ModifiedDatetime] AS [ClaimModifiedDatetime]") in sq(body)


def check_claim_parse_flag(body):
    tail = sq(body[body.index("CAST(CASE"):])
    assert sq("AS [ParseInvoiceNumber]") in tail
    assert sq("AND NOT EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] AS x WHERE x.[AttachmentId] = a.[Id])") in tail
    for t in ("BillLineItemAttachment", "BillCreditLineItemAttachment", "ExpenseLineItemAttachment"):
        assert sq(f"EXISTS (SELECT 1 FROM dbo.[{t}] AS x WHERE x.[AttachmentId] = a.[Id])") in tail, t
    assert sq("AS [IsTaxpayerDocument]") in tail
    assert sq(
        "EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] AS x WHERE x.[AttachmentId] = a.[Id])"
    ) in tail
    assert sq("INNER JOIN @Claimed AS k ON k.[Id] = a.[Id]") in tail
    assert sq("ORDER BY a.[CreatedDatetime] ASC, a.[Id] ASC") in tail


def check_claim_house_shape(body):
    assert "SET NOCOUNT ON" in body
    assert "ROLLBACK" not in body.upper()
    assert body.rstrip().endswith("END;")


def _mark_update_statement(body):
    return _between(body, "UPDATE TOP", ";")


def check_mark_header(body):
    header = sq(_header(body))
    assert sq("@Group NVARCHAR(20)") in header
    assert sq("@IncludeFailed BIT = 0") in header
    assert sq("@BatchSize INT = 500") in header
    assert sq("@DryRun BIT = 1") in header


def check_mark_dry_run_guard(body):
    assert "IF @DryRun = 0" in body
    guard = body.index("IF @DryRun = 0")
    final_select = body.rindex("SELECT")
    assert body.count("UPDATE") == 1
    assert guard < body.index("UPDATE TOP") < final_select


def check_mark_update_scope(body):
    stmt = _mark_update_statement(body)
    s = sq(stmt)
    assert sq("INNER JOIN @Members AS m ON m.[Id] = a.[Id]") in s
    assert sq("WHERE (@IncludeFailed = 1 AND a.[ExtractionStatus] = 'failed')") in s
    assert sq("OR (@IncludeFailed = 0 AND a.[ExtractionStatus] IS NULL)") in s
    assert sq("a.[ExtractionError] = CASE WHEN a.[ExtractionStatus] = 'failed' THEN NULL ELSE a.[ExtractionError] END") in s
    assert "completed" not in stmt.lower()


def check_mark_single_batch(body):
    assert "WHILE" not in body.upper()
    assert "UPDATE TOP (@BatchSize)" in body
    assert "@Affected AS [affected]" in body


def check_mark_house_shape(body):
    assert "SET NOCOUNT ON" in body
    assert "COMMIT TRANSACTION" in body
    assert "ROLLBACK" not in body.upper()
    assert body.rstrip().endswith("END;")


def check_update_error_is_head(body):
    assert sq("[ExtractionError] = @ExtractionError,") in sq(body)
    assert "WHEN [ExtractionStatus] = 'failed'" not in body


def check_update_claim_guard(body):
    assert sq("WHERE [Id] = @Id AND (@ExpectedStatus IS NULL OR [ExtractionStatus] = @ExpectedStatus)") in sq(body)
    assert sq("AND (@ExpectedModifiedDatetime IS NULL OR [ModifiedDatetime] = @ExpectedModifiedDatetime);") in sq(body)


STRUCTURAL_CHECKS = [
    (CLAIM, check_claim_header),
    (CLAIM, check_claim_groups),
    (CLAIM, check_claim_eligibility),
    (CLAIM, check_claim_candidates_snapshot),
    (CLAIM, check_claim_lock),
    (CLAIM, check_claim_order),
    (CLAIM, check_claim_update),
    (CLAIM, check_claim_token),
    (CLAIM, check_claim_parse_flag),
    (CLAIM, check_claim_house_shape),
    (MARK, check_mark_header),
    (MARK, check_mark_dry_run_guard),
    (MARK, check_mark_update_scope),
    (MARK, check_mark_single_batch),
    (MARK, check_mark_house_shape),
    (UPD, check_update_error_is_head),
    (UPD, check_update_claim_guard),
]


@pytest.mark.parametrize("proc, check", STRUCTURAL_CHECKS, ids=[c.__name__ for _, c in STRUCTURAL_CHECKS])
def test_structural_check_passes_on_the_real_sql(proc, check):
    check(_proc_batch(proc))


def test_read_pending_proc_is_byte_identical_to_head():
    assert _norm(_proc_batch("ReadAttachmentsPendingExtraction")) == _norm(HEAD_READ_PENDING_PROC)


def test_sql_file_ends_with_go_and_every_batch_holds_one_definition():
    sql = ADD_FIELDS_SQL.read_text(encoding="utf-8")
    assert sql.rstrip().upper().endswith("GO")
    for batch in _sql_batches(sql):
        assert len(re.findall(r"CREATE\s+OR\s+ALTER\s+PROCEDURE", batch, re.IGNORECASE)) <= 1
    for name in ("ReadAttachmentsPendingExtraction", CLAIM, MARK):
        assert _proc_batch(name).rstrip().endswith("END;")


# --- l. backfill CLI dry-run default -------------------------------------- #


class _FakeMarkRepo:
    calls: list = []

    def mark_pending_extraction_by_group(self, *, group, include_failed, batch_size, dry_run):
        _FakeMarkRepo.calls.append(
            {"group": group, "include_failed": include_failed, "batch_size": batch_size, "dry_run": dry_run}
        )
        return {
            "group": group,
            "affected": 0 if dry_run else 2,
            "never_extracted_pdfs": 2,
            "failed_pdfs": 1,
            "already_pending": 0,
            "completed": 4,
        }


@pytest.fixture
def backfill(monkeypatch):
    mod = importlib.import_module("scripts.attachment_extraction_backfill")
    _FakeMarkRepo.calls = []
    monkeypatch.setattr(mod, "AttachmentRepository", _FakeMarkRepo)
    return mod


def test_backfill_defaults_to_dry_run(backfill, capsys):
    rc = backfill.main(["--group", "bill"])

    assert rc == 0
    assert _FakeMarkRepo.calls == [
        {"group": "bill", "include_failed": False, "batch_size": 500, "dry_run": True}
    ]
    assert "DRY RUN" in capsys.readouterr().out


def test_backfill_apply_runs_dry_then_real(backfill):
    backfill.main(["--group", "w9", "--include-failed", "--batch-size", "100", "--apply"])

    assert [c["dry_run"] for c in _FakeMarkRepo.calls] == [True, False]
    assert _FakeMarkRepo.calls[-1] == {
        "group": "w9", "include_failed": True, "batch_size": 100, "dry_run": False
    }


def test_backfill_rejects_unknown_group(backfill):
    with pytest.raises(SystemExit):
        backfill.main(["--group", "invoice"])
    assert _FakeMarkRepo.calls == []


# --- m. repo: claim guard, claim token, backfill failed-reset only once ---------- #


def test_repo_mark_sends_include_failed_only_on_the_first_call(monkeypatch):
    events = []
    conn = _BatchConn([_count_row(affected=500), _count_row(affected=0)], events)
    calls = _patch_batched(monkeypatch, conn, events)

    repo_mod.AttachmentRepository().mark_pending_extraction_by_group(
        group="bill", include_failed=True, batch_size=500, dry_run=False
    )

    assert [c["params"]["IncludeFailed"] for c in calls] == [True, False]


def test_repo_claim_maps_the_claim_token(monkeypatch):
    _patch_repo_db(monkeypatch, _FakeCursor(rows=[_db_row(ClaimModifiedDatetime=CLAIM_TOKEN)]))

    [claimed] = repo_mod.AttachmentRepository().claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15)

    assert claimed.claim_modified_datetime == CLAIM_TOKEN


def test_repo_update_sends_the_claim_guard_and_returns_none_when_it_matches_nothing(monkeypatch):
    calls = _patch_repo_db(monkeypatch, _FakeCursor(row=None))

    result = repo_mod.AttachmentRepository().update_extraction(
        id=1,
        extraction_status="completed",
        expected_status="processing",
        expected_modified_datetime=CLAIM_TOKEN,
    )

    assert result is None
    assert calls[0]["params"]["ExpectedStatus"] == "processing"
    assert calls[0]["params"]["ExpectedModifiedDatetime"] == CLAIM_TOKEN


# --- n. no response bodies or exception text in logs (storage, DI, admin) -------- #


class _HttpResponder:
    """Stands in for httpx.Client. Every request gets one canned outcome: a status
    code (answered with the sentinel as the body) or an exception to raise."""

    def __init__(self, outcome, error_code=None):
        self._outcome = outcome
        self._error_code = error_code

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put(self, url, content=None, headers=None):
        if "restype=container" in url:
            return httpx.Response(201, request=httpx.Request("PUT", url))
        return self.request("PUT", url)

    def get(self, url, headers=None):
        return self.request("GET", url)

    def request(self, method, url, **kwargs):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        headers = {"x-ms-error-code": self._error_code} if self._error_code else None
        return httpx.Response(self._outcome, text=SENTINEL, headers=headers, request=httpx.Request(method, url))


def _storage(monkeypatch, outcome, error_code=None):
    monkeypatch.setattr(
        storage_mod.config,
        "Settings",
        lambda: types.SimpleNamespace(
            azure_storage_account_name="acct",
            azure_storage_account_key=None,
            azure_storage_sas_token="sv=2021&sig=x",
            azure_storage_container_name="container",
            azure_storage_timeout=5,
        ),
    )
    monkeypatch.setattr(storage_mod.httpx, "Client", _HttpResponder(outcome, error_code))
    return storage_mod.AzureBlobStorage()


def test_storage_upload_failure_logs_status_and_class_never_the_body(monkeypatch, caplog):
    storage = _storage(monkeypatch, 500)

    with pytest.raises(storage_mod.AzureBlobStorageError) as raised:
        storage.upload_file(blob_name="attachment-extractions/a.json", file_content=b"{}", content_type="application/json")

    logged = _log_text(caplog)
    assert SENTINEL not in str(raised.value)
    assert SENTINEL not in logged
    assert "status=500" in logged and "HTTPStatusError" in logged


def test_storage_download_failure_logs_blob_name_status_and_class_only(monkeypatch, caplog):
    storage = _storage(monkeypatch, 404)

    with pytest.raises(storage_mod.AzureBlobStorageError):
        storage.download_file("https://acct.blob.core.windows.net/container/attachment-extractions/a.json")

    logged = _log_text(caplog)
    assert SENTINEL not in logged
    assert "attachment-extractions/a.json" in logged and "status=404" in logged


def test_azure_error_code_is_logged_only_when_it_looks_like_one():
    from shared.storage import _azure_error_code

    class _Resp:
        def __init__(self, code):
            self.headers = {} if code is None else {"x-ms-error-code": code}

    assert _azure_error_code(_Resp("AuthenticationFailed")) == "AuthenticationFailed"
    assert _azure_error_code(_Resp(None)) == ""
    assert _azure_error_code(_Resp("TIN 999-88-7777 in a header")) == "unrecognized"
    assert _azure_error_code(_Resp("TIN999887777")) == "unrecognized"
    assert _azure_error_code(_Resp("TaxpayerJaneDoe")) == "unrecognized"
    assert _azure_error_code(None) == ""


def test_storage_error_log_carries_azure_error_code_never_the_body(monkeypatch, caplog):
    storage = _storage(monkeypatch, 404, error_code="BlobNotFound")

    with pytest.raises(storage_mod.AzureBlobStorageError):
        storage.download_file("https://acct.blob.core.windows.net/container/attachment-extractions/a.json")

    logged = _log_text(caplog)
    assert SENTINEL not in logged
    assert "status=404" in logged and "code=BlobNotFound" in logged and "exc=HTTPStatusError" in logged


def test_storage_download_transport_error_logs_class_never_the_message(monkeypatch, caplog):
    storage = _storage(monkeypatch, httpx.ConnectError(f"reset {SENTINEL}"))

    with pytest.raises(storage_mod.AzureBlobStorageError):
        storage.download_file("https://acct.blob.core.windows.net/container/attachment-extractions/a.json")

    logged = _log_text(caplog)
    assert SENTINEL not in logged
    assert "exc=ConnectError" in logged


def test_di_retry_warning_logs_exception_class_not_message(monkeypatch, caplog):
    monkeypatch.setattr(di_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(di_client.httpx, "Client", _HttpResponder(httpx.ConnectError(f"reset {SENTINEL}")))

    with pytest.raises(DocumentIntelligenceError):
        di_client._do_request("POST", "https://di.example/analyze", headers={}, content=b"%PDF")

    logged = _log_text(caplog)
    assert SENTINEL not in logged
    assert "raised ConnectError" in logged


def test_di_throttle_warning_logs_status_never_the_body(monkeypatch, caplog):
    monkeypatch.setattr(di_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(di_client.httpx, "Client", _HttpResponder(503))

    response = di_client._do_request("GET", "https://di.example/operations/1", headers={})

    assert response.status_code == 503
    assert SENTINEL not in _log_text(caplog)


def test_admin_tick_failure_returns_only_the_exception_class_name(monkeypatch, caplog):
    admin = importlib.import_module("shared.api.admin")

    class _Exploding:
        def extract_pending(self, *, limit):
            raise ValueError(f"document text {SENTINEL}")

    monkeypatch.setattr(es, "AttachmentExtractionService", _Exploding)

    result = asyncio.run(admin.attachment_extract_tick_router(limit=3))

    assert result == {"status": "error", "job": "attachment.extract.tick", "error": "ValueError"}
    assert SENTINEL not in _log_text(caplog)


def test_admin_tick_limit_is_capped_at_five(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    admin = importlib.import_module("shared.api.admin")
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin._require_drain_secret] = lambda: None
    client = TestClient(app)
    path = next(r.path for r in admin.router.routes if r.path.endswith("/attachment/extract/tick"))

    assert client.post(path, params={"limit": 6}).status_code == 422
    assert client.post(path, params={"limit": 0}).status_code == 422


# --- k. blob isolation and log hygiene (fix round 3) ----------------------- #


class _TokenRepo:
    """Models the guarded UpdateAttachmentExtraction: a write lands only on the current claim token."""

    def __init__(self, current_token):
        self.current_token = current_token
        self.stored = None
        self.rejected = []

    def update_extraction(self, **kwargs):
        if kwargs.get("expected_modified_datetime") != self.current_token:
            self.rejected.append(kwargs)
            return None
        self.stored = kwargs
        return types.SimpleNamespace(id=kwargs["id"])


def test_stale_worker_writes_its_own_blob_and_deletes_it_without_touching_the_current_one(monkeypatch):
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    token_a = CLAIM_TOKEN
    token_b = CLAIM_TOKEN + dt.timedelta(minutes=20)
    att_a = _att(1, content_type="application/pdf", claim_modified_datetime=token_a)
    att_b = _att(1, content_type="application/pdf", claim_modified_datetime=token_b)
    repo = _TokenRepo(current_token=token_b)
    storage = _FakeStorage(downloads={att_a.blob_url: pdf})
    svc = _service(repo, storage=storage, di=_FakeDI(forbid=True))

    done_b = svc._extract(att_b)
    done_a = svc._extract(att_a)

    assert done_b["status"] == "completed"
    assert done_a == {"status": "skipped", "reason": "claim_lost", "attachment_id": 1}
    blob_b, blob_a = storage.uploads
    assert blob_a["blob_name"] != blob_b["blob_name"]
    assert re.fullmatch(
        r"attachment-extractions/aaaaaaaa-0000-0000-0000-000000000001/[0-9a-f]{32}\.json",
        blob_a["blob_name"],
    )
    assert repo.stored["extracted_text_blob_url"] == f"https://blob.example/{blob_b['blob_name']}"
    assert storage.deleted == [f"https://blob.example/{blob_a['blob_name']}"]


def test_a_raising_terminal_write_keeps_the_blob_because_the_commit_outcome_is_unknown(monkeypatch):
    # A terminal write can raise AFTER the database committed (e.g. the commit
    # acknowledgement is lost). The row may now reference this blob, so it must be
    # kept; only a definitive 0-row result (claim_lost) proves it was never published.
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    att = _att(1, content_type="application/pdf")
    repo = _FakeRepo(rows=[att], fail_terminal=True)
    storage = _FakeStorage(downloads={att.blob_url: pdf})

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True))._extract(att)

    assert out == {"status": "failed", "reason": "terminal_write_failed", "attachment_id": 1}
    assert len(storage.uploads) == 1
    assert storage.deleted == []


def test_orphan_blob_delete_failure_logs_blob_name_and_class_never_the_message(monkeypatch, caplog):
    pdf = _text_pdf_bytes(["ACME SUPPLY COMPANY", "Invoice # 12345", "Total 10.00"])
    att = _att(1, content_type="application/pdf")
    repo = _FakeRepo(rows=[att], lose_claim=True)
    storage = _FakeStorage(
        downloads={att.blob_url: pdf},
        delete_error=RuntimeError(f"delete refused for {SENTINEL}"),
    )
    caplog.set_level(logging.DEBUG)

    out = _service(repo, storage=storage, di=_FakeDI(forbid=True))._extract(att)

    assert out["reason"] == "claim_lost"
    blob_name = storage.uploads[0]["blob_name"]
    messages = [r.getMessage() for r in caplog.records if "orphan" in r.getMessage()]
    assert messages == [
        f"attachment_extraction.orphan_blob_delete_failed blob={blob_name} exc=RuntimeError"
    ]
    assert SENTINEL not in _log_text(caplog)


# --- l. repository logs never carry exception text ------------------------ #


class _ExplodingRow:
    def __getattr__(self, name):
        raise RuntimeError(f"row broke on {SENTINEL}")


def _record_text(record):
    return "\n".join(
        [record.getMessage(), repr(record.args), record.exc_text or "", repr(record.exc_info)]
    )


def _repo_calls():
    repo = repo_mod.AttachmentRepository()
    return {
        "update_extraction": lambda: repo.update_extraction(id=1, extraction_status="completed"),
        "claim_for_extraction": lambda: repo.claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15),
        "mark_pending_extraction_by_group": lambda: repo.mark_pending_extraction_by_group(
            group="bill", include_failed=False, batch_size=10, dry_run=True
        ),
        "_from_db": lambda: repo._from_db(_ExplodingRow()),
    }


@pytest.mark.parametrize("method", list(_repo_calls()))
def test_repo_extraction_path_logs_class_only_and_still_raises(method, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError(f"connection string leaked {SENTINEL}")

    monkeypatch.setattr(repo_mod, "get_connection", boom)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(Exception):
        _repo_calls()[method]()

    assert caplog.records, f"{method} logged nothing"
    for record in caplog.records:
        assert SENTINEL not in _record_text(record)
