"""U-535b b2: review API router + review service (fakes only: no DB, no Box, no DI)."""
from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.attachment_backfill.api.router import PDF_HEADERS, get_review_service, router
from entities.attachment_backfill.business.review import (
    MAX_VERIFY_BYTES,
    AttachmentBackfillReviewService,
)
from entities.auth.business.service import get_current_user_api

RUN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CAND = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
RV = base64.b64encode(b"\x00\x00\x00\x00\x00\x00\x07\xd1").decode()
STORED_FILE_ID = "box-stored-123"
SECRET_PHRASE = "ZQXWVU-PRIVATE-PDF-PHRASE"
PDF_BYTES = b"%PDF-1.7\n" + b"0" * 64
REVIEWER_INT_ID = 987654
REVIEWER_PUBLIC_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

PREFIX = router.prefix
ROUTES = [
    ("GET", f"{PREFIX}/runs", None),
    ("GET", f"{PREFIX}/runs/{RUN}/facets", None),
    ("GET", f"{PREFIX}/runs/{RUN}/queue", None),
    ("GET", f"{PREFIX}/candidates/{CAND}", None),
    ("GET", f"{PREFIX}/candidates/{CAND}/pdf", None),
    ("POST", f"{PREFIX}/candidates/{CAND}/review", {"decision": "approved", "row_version": RV}),
    ("POST", f"{PREFIX}/candidates/{CAND}/undo", {"row_version": RV}),
]


def _detail_row(**overrides) -> dict:
    row = {
        "Id": 7, "PublicId": uuid.UUID(CAND), "RowVersion": b"\x00" * 7 + b"\x01",
        "RunPublicId": uuid.UUID(RUN), "RunStatus": "ready",
        "BillPublicId": uuid.uuid4(), "BillNumber": "1001", "BillDate": datetime(2023, 1, 2), "BillYear": 2023,
        "TotalAmount": Decimal("1234.50"), "VendorName": "Acme Lumber",
        "ProjectPublicId": uuid.uuid4(), "ProjectName": "Alpha", "ProjectNames": "Alpha",
        "SourceSha256": hashlib.sha256(PDF_BYTES).hexdigest().upper(),
        "SourceFileId": STORED_FILE_ID, "SourceFileName": "a.pdf", "SourceFileSize": 1000, "MatchTier": "A",
        "CheckStatus": "checked", "InvoiceSeenCount": 1, "AmountSeenCount": 1, "VendorSeen": True,
        "OtherInvoiceLabelCount": 0, "StatementLike": False, "UsedOcr": False, "EvidenceGroup": "clean",
        "ReviewStatus": "pending", "RejectReason": None, "ReviewedByUserId": None, "ReviewedByUserPublicId": None,
        "ReviewedDatetime": None,
        "Linked": False,
    }
    row.update(overrides)
    return row


class FakeRepo:
    def __init__(self):
        self.calls: list[tuple] = []
        self.detail: dict | None = _detail_row()
        self.review_row: dict = {
            "Id": 7, "PublicId": uuid.UUID(CAND), "RowVersion": b"\x00" * 7 + b"\x02", "ReviewStatus": "approved",
            "RejectReason": None, "ReviewedByUserId": REVIEWER_INT_ID,
            "ReviewedByUserPublicId": uuid.UUID(REVIEWER_PUBLIC_ID), "ReviewedDatetime": datetime(2026, 10, 30), "RefusedReason": None,
        }
        self.queue_rows: list[dict] = []

    def read_runs_for_review(self):
        return [{"PublicId": uuid.UUID(RUN), "Source": "box", "Status": "ready", "CandidateCount": 3, "Pending": 3}]

    def read_run_by_public_id(self, public_id):
        return {"Id": 1, "PublicId": public_id} if public_id == RUN else None

    def read_run_facets(self, *, run_public_id):
        return [{"EvidenceGroup": "clean", "ProjectPublicId": None, "ProjectName": None, "BillYear": 2023,
                 "ReviewStatus": "pending", "CandidateCount": 3}]

    def read_queue(self, **kwargs):
        self.calls.append(("read_queue", kwargs))
        return self.queue_rows[: kwargs["top"]]

    def read_candidate_detail(self, *, public_id):
        self.calls.append(("read_candidate_detail", public_id))
        return dict(self.detail) if self.detail and public_id == CAND else None

    def update_candidate_review(self, **kwargs):
        self.calls.append(("update_candidate_review", kwargs))
        return dict(self.review_row)

    def undo_candidate_review(self, **kwargs):
        self.calls.append(("undo_candidate_review", kwargs))
        return dict(self.review_row)


class Fetch:
    def __init__(self, content=PDF_BYTES, exc: Exception | None = None):
        self.content, self.exc, self.calls = content, exc, []

    def __call__(self, file_id):
        self.calls.append(file_id)
        if self.exc:
            raise self.exc
        return self.content


def _text_layer(_content: bytes) -> str:
    return (
        f"ACME LUMBER statement-free invoice text {SECRET_PHRASE}\n"
        f"Invoice # 1001 amount due $1,234.50 {SECRET_PHRASE} padding padding padding padding\n" * 10
    )


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def fetch():
    return Fetch()


@pytest.fixture
def ocr_calls():
    return []


@pytest.fixture
def client(repo, fetch, ocr_calls):
    def _ocr(content):
        ocr_calls.append(len(content))
        return ""

    service = AttachmentBackfillReviewService(repo, fetch_bytes=fetch, text_layer=_text_layer, ocr=_ocr)
    app.dependency_overrides[get_review_service] = lambda: service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_review_service, None)
    app.dependency_overrides.pop(get_current_user_api, None)


def _as(is_system_admin: bool, user_id=17):
    async def _fake_user():
        return {"sub": "s", "user_id": user_id, "is_system_admin": is_system_admin, "company_id": 1}

    app.dependency_overrides[get_current_user_api] = _fake_user


def _send(client, method, path, body=None, **kw):
    return client.request(method, path, json=body, **kw)


# -- auth ---------------------------------------------------------------------------------------- #


def test_route_table_is_exactly_the_seven_routes():
    got = sorted((m, r.path) for r in router.routes for m in r.methods)
    assert got == sorted((m, p) for m, p, _ in [(m, p.replace(CAND, "{candidate_public_id}").replace(RUN, "{run_public_id}"), b) for m, p, b in ROUTES])


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_unauthenticated_is_401(client, method, path, body):
    app.dependency_overrides.pop(get_current_user_api, None)
    assert _send(client, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_non_admin_is_403_and_touches_nothing(client, repo, fetch, method, path, body):
    _as(False)
    assert _send(client, method, path, body).status_code == 403
    assert repo.calls == [] and fetch.calls == []


# -- reads --------------------------------------------------------------------------------------- #


def test_runs_and_facets_envelopes(client):
    _as(True)
    runs = client.get(f"{PREFIX}/runs").json()
    assert runs["data"][0]["public_id"] == RUN and runs["data"][0]["candidate_count"] == 3
    facets = client.get(f"{PREFIX}/runs/{RUN}/facets").json()
    assert facets["data"] == [{"evidence_group": "clean", "project_public_id": None, "project_name": None,
                               "bill_year": 2023, "review_status": "pending", "count": 3}]


def test_unknown_run_is_404(client):
    _as(True)
    other = str(uuid.uuid4())
    assert client.get(f"{PREFIX}/runs/{other}/facets").status_code == 404
    assert client.get(f"{PREFIX}/runs/{other}/queue").status_code == 404


def test_queue_params_cursor_and_limit_cap(client, repo):
    _as(True)
    repo.queue_rows = [_detail_row(PublicId=uuid.uuid4()) for _ in range(4)]
    proj = str(uuid.uuid4())
    after = str(uuid.uuid4())
    body = client.get(
        f"{PREFIX}/runs/{RUN}/queue", params={"group": "clean", "project_id": proj, "year": 2023, "after": after, "limit": 3}
    ).json()
    kwargs = repo.calls[-1][1]
    assert kwargs == {
        "run_public_id": RUN, "evidence_group": "clean", "project_public_id": proj, "no_project": False,
        "bill_year": 2023, "no_bill_year": False, "review_status": "pending", "after_public_id": after, "top": 4,
    }
    assert len(body["data"]) == 3 and body["next_after"] == body["data"][-1]["public_id"]
    assert "source_file_id" not in body["data"][0] and "id" not in body["data"][0]
    assert body["data"][0]["total_amount"] == "1234.50"
    body = client.get(f"{PREFIX}/runs/{RUN}/queue", params={"project_id": "none", "review_status": "all"}).json()
    assert repo.calls[-1][1]["no_project"] is True and repo.calls[-1][1]["review_status"] is None
    assert body["next_after"] is None


@pytest.mark.parametrize(
    "params",
    [{"limit": 201}, {"limit": 0}, {"group": "verified"}, {"review_status": "done"}, {"after": "x"}, {"project_id": "12"},
     {"year": "abc"}, {"year": "1800"}],
)
def test_queue_bad_params_are_422(client, params):
    _as(True)
    assert client.get(f"{PREFIX}/runs/{RUN}/queue", params=params).status_code == 422


def test_non_uuid_path_ids_are_422(client):
    _as(True)
    assert client.get(f"{PREFIX}/candidates/7").status_code == 422
    assert client.get(f"{PREFIX}/runs/7/queue").status_code == 422


def test_candidate_detail_with_snippets_and_no_text_in_logs(client, fetch, caplog):
    _as(True)
    caplog.set_level(logging.DEBUG)
    resp = client.get(f"{PREFIX}/candidates/{CAND}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["snippet_error"] is None and data["snippets"]
    assert {s["kind"] for s in data["snippets"]} >= {"invoice_number", "amount", "vendor"}
    assert len(data["snippets"]) <= 20
    assert fetch.calls == [STORED_FILE_ID]
    assert SECRET_PHRASE in resp.text  # the reviewer sees it ...
    assert SECRET_PHRASE not in caplog.text  # ... but it is never logged
    assert "1,234.50" not in caplog.text


def test_detail_unknown_candidate_is_404(client, repo):
    _as(True)
    repo.detail = None
    assert client.get(f"{PREFIX}/candidates/{CAND}").status_code == 404


def test_detail_fetch_failure_returns_code_without_exception_text(client, fetch, caplog):
    _as(True)
    fetch.exc = RuntimeError("box said SECRET-EXC-TEXT")
    caplog.set_level(logging.DEBUG)
    resp = client.get(f"{PREFIX}/candidates/{CAND}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["snippets"] is None and data["snippet_error"] == "fetch_failed"
    assert "SECRET-EXC-TEXT" not in resp.text and "SECRET-EXC-TEXT" not in caplog.text


def test_detail_ocr_failure_and_too_large_and_unreadable(repo, fetch):
    def bad_ocr(_c):
        raise RuntimeError("di down")

    svc = AttachmentBackfillReviewService(repo, fetch_bytes=fetch, text_layer=lambda c: "", ocr=bad_ocr)
    assert svc.candidate_detail(CAND)["snippet_error"] == "ocr_failed"
    svc = AttachmentBackfillReviewService(repo, fetch_bytes=fetch, text_layer=lambda c: "", ocr=lambda c: "tiny")
    assert svc.candidate_detail(CAND)["snippet_error"] == "unreadable"
    repo.detail = _detail_row(SourceFileSize=MAX_VERIFY_BYTES + 1)
    fetch.calls.clear()
    out = svc.candidate_detail(CAND)
    assert out["snippet_error"] == "too_large" and out["snippets"] is None and fetch.calls == []


def test_detail_uses_ocr_only_when_b1_would(client, ocr_calls):
    _as(True)
    client.get(f"{PREFIX}/candidates/{CAND}")
    assert ocr_calls == []  # long text layer with the invoice # seen: no OCR


# -- pdf ----------------------------------------------------------------------------------------- #


def test_pdf_uses_stored_file_id_and_exact_headers(client, fetch):
    _as(True)
    resp = client.get(
        f"{PREFIX}/candidates/{CAND}/pdf",
        params={"file_id": "999", "source_file_id": "999", "SourceFileId": "999"},
        headers={"X-Box-File-Id": "999"},
    )
    assert resp.status_code == 200
    assert resp.content == PDF_BYTES
    assert fetch.calls == [STORED_FILE_ID]
    assert resp.headers["content-type"] == "application/pdf"
    for key, value in PDF_HEADERS.items():
        assert resp.headers[key] == value
    assert resp.headers["Content-Disposition"] == 'inline; filename="candidate.pdf"'
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cache-Control"] == "no-store"
    assert "a.pdf" not in resp.headers["Content-Disposition"]


def test_pdf_unknown_candidate_is_404(client, repo, fetch):
    _as(True)
    repo.detail = None
    assert client.get(f"{PREFIX}/candidates/{CAND}/pdf").status_code == 404
    assert fetch.calls == []


def test_pdf_over_25mb_by_stored_size_is_rejected_without_download(client, repo, fetch):
    _as(True)
    repo.detail = _detail_row(SourceFileSize=MAX_VERIFY_BYTES + 1)
    resp = client.get(f"{PREFIX}/candidates/{CAND}/pdf")
    assert resp.status_code == 413 and resp.json()["error_code"] == "too_large"
    assert fetch.calls == []


def test_pdf_over_25mb_downloaded_is_rejected(client, repo, fetch):
    _as(True)
    repo.detail = _detail_row(SourceFileSize=None)
    fetch.content = b"%PDF-" + b"0" * MAX_VERIFY_BYTES
    assert client.get(f"{PREFIX}/candidates/{CAND}/pdf").status_code == 413


def test_pdf_fetch_failure_and_non_pdf(client, fetch):
    _as(True)
    fetch.exc = RuntimeError("SECRET-EXC-TEXT")
    resp = client.get(f"{PREFIX}/candidates/{CAND}/pdf")
    assert resp.status_code == 502 and "SECRET-EXC-TEXT" not in resp.text
    fetch.exc, fetch.content = None, b"<html>not a pdf</html>"
    assert client.get(f"{PREFIX}/candidates/{CAND}/pdf").status_code == 422


# -- review / undo -------------------------------------------------------------------------------- #


def test_review_approve_passes_decoded_row_version_and_caller_user_id(client, repo):
    _as(True, user_id=33)
    resp = client.post(f"{PREFIX}/candidates/{CAND}/review", json={"decision": "approved", "row_version": RV})
    assert resp.status_code == 200
    name, kwargs = repo.calls[-1]
    assert name == "update_candidate_review"
    assert kwargs == {
        "public_id": CAND, "decision": "approved", "reject_reason": None,
        "row_version": base64.b64decode(RV), "reviewed_by_user_id": 33,
    }
    data = resp.json()["data"]
    assert data["review_status"] == "approved" and data["row_version"] == base64.b64encode(b"\x00" * 7 + b"\x02").decode()


def test_review_reject_with_reason(client, repo):
    _as(True)
    resp = client.post(
        f"{PREFIX}/candidates/{CAND}/review",
        json={"decision": "rejected", "reject_reason": "multiple_invoices", "row_version": RV},
    )
    assert resp.status_code == 200 and repo.calls[-1][1]["reject_reason"] == "multiple_invoices"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "rejected", "row_version": RV},  # reason required
        {"decision": "approved", "reject_reason": "other", "row_version": RV},  # reason forbidden
        {"decision": "rejected", "reject_reason": "bogus", "row_version": RV},
        {"decision": "maybe", "row_version": RV},
        {"decision": "approved"},  # no row_version
        {"decision": "approved", "row_version": "not base64!"},
        {"decision": "approved", "row_version": base64.b64encode(b"\x01\x02").decode()},  # not 8 bytes
        {"decision": "approved", "row_version": RV, "attachment_id": 5},  # unknown field
    ],
)
def test_review_validation_is_422_and_writes_nothing(client, repo, body):
    _as(True)
    resp = client.post(f"{PREFIX}/candidates/{CAND}/review", json=body)
    assert resp.status_code == 422
    assert not any(c[0] == "update_candidate_review" for c in repo.calls)


@pytest.mark.parametrize(
    "code,status",
    [
        ("not_found", 404), ("stale", 409), ("not_pending", 409), ("run_not_ready", 409), ("linked", 409),
        ("bill_missing", 409), ("bill_claimed_elsewhere", 409), ("invalid", 422),
    ],
)
def test_review_refusal_status_mapping(client, repo, code, status):
    _as(True)
    repo.review_row = {"Id": None, "PublicId": None, "RefusedReason": code}
    resp = client.post(f"{PREFIX}/candidates/{CAND}/review", json={"decision": "approved", "row_version": RV})
    assert resp.status_code == status and resp.json()["error_code"] == code


@pytest.mark.parametrize(
    "code,status",
    [
        ("not_found", 404), ("stale", 409), ("not_reviewed", 409), ("run_not_ready", 409), ("linked", 409),
        ("bill_claimed_elsewhere", 409),
    ],
)
def test_undo_refusal_status_mapping(client, repo, code, status):
    _as(True)
    repo.review_row = {"Id": None, "PublicId": None, "RefusedReason": code}
    resp = client.post(f"{PREFIX}/candidates/{CAND}/undo", json={"row_version": RV})
    assert resp.status_code == status and resp.json()["error_code"] == code


def test_undo_happy_path_and_validation(client, repo):
    _as(True)
    repo.review_row.update(ReviewStatus="pending", ReviewedByUserId=None, ReviewedDatetime=None)
    resp = client.post(f"{PREFIX}/candidates/{CAND}/undo", json={"row_version": RV})
    assert resp.status_code == 200 and resp.json()["data"]["review_status"] == "pending"
    assert repo.calls[-1] == ("undo_candidate_review", {"public_id": CAND, "row_version": base64.b64decode(RV)})
    assert client.post(f"{PREFIX}/candidates/{CAND}/undo", json={}).status_code == 422


def test_review_layer_imports_no_attachment_bill_or_outbox_writer():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "entities/attachment_backfill"
    for rel in ("business/review.py", "business/snippets.py", "api/router.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        for mod in modules:
            assert not mod.startswith(("entities.attachment.", "entities.bill", "integrations.box.outbox")), (rel, mod)
            assert "outbox" not in mod, (rel, mod)


# -- fix round 1 --------------------------------------------------------------------------------- #


def test_detail_content_hash_matches_check(client):
    _as(True)
    data = client.get(f"{PREFIX}/candidates/{CAND}").json()["data"]
    assert data["content_matches_check"] is True and data["snippets"]


def test_detail_content_changed_since_check_returns_no_snippets(client, repo):
    _as(True)
    repo.detail = _detail_row(SourceSha256="ab" * 32)
    data = client.get(f"{PREFIX}/candidates/{CAND}").json()["data"]
    assert data["content_matches_check"] is False
    assert data["snippets"] is None and data["snippet_error"] == "content_changed"


def test_detail_without_stored_hash_or_fetch_is_null(client, repo, fetch):
    _as(True)
    repo.detail = _detail_row(SourceSha256=None)
    data = client.get(f"{PREFIX}/candidates/{CAND}").json()["data"]
    assert data["content_matches_check"] is None and data["snippets"]
    repo.detail = _detail_row()
    fetch.exc = RuntimeError("x")
    data = client.get(f"{PREFIX}/candidates/{CAND}").json()["data"]
    assert data["content_matches_check"] is None and data["snippet_error"] == "fetch_failed"


@pytest.mark.parametrize("stored,expected", [("same", "true"), ("ab" * 32, "false"), (None, "unknown")])
def test_pdf_content_matches_check_header(client, repo, stored, expected):
    _as(True)
    repo.detail = _detail_row(SourceSha256=hashlib.sha256(PDF_BYTES).hexdigest() if stored == "same" else stored)
    resp = client.get(f"{PREFIX}/candidates/{CAND}/pdf")
    assert resp.status_code == 200 and resp.content == PDF_BYTES
    assert resp.headers["X-Content-Matches-Check"] == expected


def test_pdf_csp_sandbox_header(client):
    _as(True)
    assert client.get(f"{PREFIX}/candidates/{CAND}/pdf").headers["Content-Security-Policy"] == "sandbox"


def _assert_no_reviewer_int(resp):
    assert str(REVIEWER_INT_ID) not in resp.text
    assert "reviewed_by_user_id" not in resp.text


def test_reviewer_is_exposed_by_public_id_only(client, repo):
    _as(True, user_id=REVIEWER_INT_ID)
    reviewed = dict(ReviewStatus="approved", ReviewedByUserId=REVIEWER_INT_ID,
                    ReviewedByUserPublicId=uuid.UUID(REVIEWER_PUBLIC_ID))
    repo.detail = _detail_row(**reviewed)
    repo.queue_rows = [_detail_row(**reviewed)]
    for resp in (
        client.get(f"{PREFIX}/candidates/{CAND}"),
        client.get(f"{PREFIX}/runs/{RUN}/queue", params={"review_status": "all"}),
        client.post(f"{PREFIX}/candidates/{CAND}/review", json={"decision": "approved", "row_version": RV}),
        client.post(f"{PREFIX}/candidates/{CAND}/undo", json={"row_version": RV}),
    ):
        assert resp.status_code == 200, resp.text
        _assert_no_reviewer_int(resp)
        assert REVIEWER_PUBLIC_ID in resp.text


def test_queue_year_none_selects_stale_rows(client, repo):
    _as(True)
    client.get(f"{PREFIX}/runs/{RUN}/queue", params={"year": "none"})
    kwargs = repo.calls[-1][1]
    assert kwargs["bill_year"] is None and kwargs["no_bill_year"] is True
    client.get(f"{PREFIX}/runs/{RUN}/queue", params={"year": "2023"})
    kwargs = repo.calls[-1][1]
    assert kwargs["bill_year"] == 2023 and kwargs["no_bill_year"] is False


# -- mutation pins ------------------------------------------------------------------------------- #


@pytest.mark.parametrize("stored_size", [None, 1000])
def test_detail_over_25mb_downloaded_is_too_large_without_text_extraction(repo, stored_size):
    """X11: the detail path checks the downloaded size (after the hash check) before reading any text."""
    big = b"%PDF-" + b"0" * MAX_VERIFY_BYTES
    repo.detail = _detail_row(SourceFileSize=stored_size, SourceSha256=hashlib.sha256(big).hexdigest())
    text_calls, ocr_calls = [], []

    def text_layer(content):
        text_calls.append(len(content))
        return _text_layer(content)

    def ocr(content):
        ocr_calls.append(len(content))
        return ""

    svc = AttachmentBackfillReviewService(repo, fetch_bytes=Fetch(content=big), text_layer=text_layer, ocr=ocr)
    out = svc.candidate_detail(CAND)
    assert out["content_matches_check"] is True
    assert out["snippet_error"] == "too_large" and out["snippets"] is None
    assert text_calls == [] and ocr_calls == []
