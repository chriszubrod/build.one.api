"""U-218e — route QboAttachableClient through the shared QboHttpClient multipart seam."""
import json
import re
from email import message_from_bytes
from unittest.mock import MagicMock, patch

import httpx
import pytest

from entities.bill.business.service import BillService
from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.base import retry as retry_module
from integrations.intuit.qbo.base.client import QboHttpClient, _TIMEOUT_TIERS
from integrations.intuit.qbo.base.errors import (
    QboMalformedResponseError,
    QboRateLimitError,
    QboServerError,
    QboServiceUnavailableError,
    QboTimeoutError,
    QboTransportError,
    QboWriteRefusedError,
)
from integrations.intuit.qbo.base.retry import (
    RetryPolicy,
    TIER_A_REQUEST_CEILING_SECONDS,
    TIER_C_REQUEST_CEILING_SECONDS,
    execute_with_retry,
)

REALM_ID = "realm-test"


def _auth_client(http_client: httpx.Client) -> QboHttpClient:
    auth = MagicMock()
    auth.ensure_valid_token_classified.return_value = (
        MagicMock(access_token="tok"),
        MagicMock(value="none"),
    )
    return QboHttpClient(
        realm_id=REALM_ID,
        auth_service=auth,
        http_client=http_client,
        api_budget=MagicMock(),
    )


def _upload_success_response() -> dict:
    return {
        "AttachableResponse": [
            {"Attachable": {"Id": "99", "SyncToken": "0", "FileName": "a.pdf"}}
        ]
    }


def _multipart_message(body: bytes, content_type: str):
    msg = message_from_bytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
    )
    if not msg.is_multipart():
        raise AssertionError(f"expected multipart body, got {content_type!r}")
    return msg


def _get_multipart_part(body: bytes, content_type: str, part_name: str):
    for part in _multipart_message(body, content_type).walk():
        disposition = part.get("Content-Disposition", "")
        match = re.search(r'name="([^"]+)"', disposition)
        if match and match.group(1) == part_name:
            return part
    raise AssertionError(f"multipart part {part_name!r} not found")


def _multipart_named_parts_in_order(body: bytes, content_type: str) -> list[str]:
    names: list[str] = []
    for part in _multipart_message(body, content_type).walk():
        disposition = part.get("Content-Disposition", "")
        match = re.search(r'name="([^"]+)"', disposition)
        if match:
            names.append(match.group(1))
    return names


def _decode_multipart_part_json(body: bytes, content_type: str, part_name: str) -> dict:
    """Extract and JSON-decode a named part from a captured multipart/form-data body."""
    part = _get_multipart_part(body, content_type, part_name)
    payload = part.get_payload(decode=True)
    if payload is None:
        raise AssertionError(f"multipart part {part_name!r} had no payload")
    return json.loads(payload.decode("utf-8"))


# --------------------------------------------------------------------------- #
# 1. Wire-shape: multipart order + boundary
# --------------------------------------------------------------------------- #


def test_upload_multipart_wire_shape(monkeypatch):
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")
    captured = {}
    file_content = b"%PDF-1.4 distinctive-wire-bytes-218e"
    filename = "invoice-42.pdf"
    part_content_type = "application/pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=_upload_success_response())

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    shared = _auth_client(client)
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)

    attachable.upload_attachable(
        file_content=file_content,
        filename=filename,
        content_type=part_content_type,
        entity_type="Bill",
        entity_id="123",
    )

    req = captured["request"]
    content_type = req.headers.get("content-type", "")
    assert content_type.startswith("multipart/form-data; boundary=")
    body = req.read()

    assert _multipart_named_parts_in_order(body, content_type) == [
        "file_metadata_01",
        "file_content_01",
    ]

    meta_part = _get_multipart_part(body, content_type, "file_metadata_01")
    content_part = _get_multipart_part(body, content_type, "file_content_01")

    meta_disposition = meta_part.get("Content-Disposition", "")
    content_disposition = content_part.get("Content-Disposition", "")
    assert 'filename="' + filename + '"' in content_disposition
    assert "filename=" not in meta_disposition.lower()

    assert meta_part.get_content_type() == "application/json"
    assert content_part.get_content_type() == part_content_type

    assert content_part.get_payload(decode=True) == file_content
    assert file_content in body

    metadata = _decode_multipart_part_json(body, content_type, "file_metadata_01")
    assert metadata == {
        "AttachableRef": [{"EntityRef": {"type": "Bill", "value": "123"}}],
        "FileName": filename,
        "ContentType": part_content_type,
    }
    assert metadata["AttachableRef"][0]["EntityRef"]["type"] == "Bill"
    assert metadata["AttachableRef"][0]["EntityRef"]["value"] == "123"
    assert metadata["AttachableRef"][0]["EntityRef"]["type"] != "123"
    assert metadata["AttachableRef"][0]["EntityRef"]["value"] != "Bill"


# --------------------------------------------------------------------------- #
# 2. Mutation-killing: Content-Type guard (files-is-None, not json_body)
# --------------------------------------------------------------------------- #


def _capture_request(handler_payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        handler_payload["request"] = request
        if request.method == "GET":
            return httpx.Response(200, json={"QueryResponse": {}})
        if "upload" in str(request.url):
            return httpx.Response(200, json=_upload_success_response())
        return httpx.Response(200, json={"Bill": {"Id": "1", "SyncToken": "0"}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_json_post_sends_application_json_and_body(monkeypatch):
    """MUT-A: JSON POST must send application/json + serialized body."""
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")
    captured: dict = {}
    shared = _auth_client(_capture_request(captured))
    payload = {"Id": "1", "SyncToken": "0"}
    shared.post("bill", json=payload, operation_name="qbo.bill.update")

    req = captured["request"]
    assert req.headers.get("content-type") == "application/json"
    assert json.loads(req.content) == payload


def test_get_without_body_still_sends_application_json(monkeypatch):
    """
    MUT-A discriminant: GET has neither json_body nor files, yet the
    files-is-None guard must still stamp application/json so legacy JSON
    clients stay byte-identical. Flipping to json_body-is-not-None leaves
    this header unset — the naive JSON-post-only assertion misses that.
    """
    captured: dict = {}
    shared = _auth_client(_capture_request(captured))
    shared.get("bill/1", operation_name="qbo.bill.get")

    req = captured["request"]
    assert req.headers.get("content-type") == "application/json"
    assert req.content in (b"", None)


def test_empty_json_post_still_sends_application_json(monkeypatch):
    """Companion MUT-A case: POST with json=None is still a JSON client call."""
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")
    captured: dict = {}
    shared = _auth_client(_capture_request(captured))
    shared.post("bill", operation_name="qbo.bill.create")

    req = captured["request"]
    assert req.headers.get("content-type") == "application/json"


def test_multipart_upload_content_type_not_application_json(monkeypatch):
    """MUT-A: multipart must let httpx own the boundary; no hand-set JSON type."""
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")
    captured: dict = {}
    shared = _auth_client(_capture_request(captured))
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)
    attachable.upload_attachable(
        file_content=b"%PDF-1.4 payload",
        filename="a.pdf",
        content_type="application/pdf",
        entity_type="Bill",
        entity_id="123",
    )

    req = captured["request"]
    content_type = req.headers.get("content-type", "")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert content_type != "application/json"


def _timeout_from_request(request: httpx.Request) -> dict:
    return request.extensions["timeout"]


# --------------------------------------------------------------------------- #
# 2b. Mutation-killing: upload default timeout tier C (120s), GET stays A
# --------------------------------------------------------------------------- #


def test_upload_request_uses_tier_c_timeout(monkeypatch):
    """MUT-B: post_multipart default must thread tier-C (120s read/write)."""
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")
    captured: dict = {}
    shared = _auth_client(_capture_request(captured))
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)
    attachable.upload_attachable(
        file_content=b"%PDF-1.4 payload",
        filename="a.pdf",
        content_type="application/pdf",
        entity_type="Bill",
        entity_id="123",
    )

    timeout = _timeout_from_request(captured["request"])
    assert timeout["read"] == 120.0
    assert timeout["write"] == 120.0
    assert timeout["read"] != 30.0
    assert timeout["write"] != 30.0


def test_get_request_uses_tier_a_timeout():
    """MUT-B companion: ordinary GET on the same client stays tier A (30s)."""
    captured: dict = {}
    shared = _auth_client(_capture_request(captured))
    shared.get("attachable/1", operation_name="qbo.attachable.get")

    timeout = _timeout_from_request(captured["request"])
    assert timeout["read"] == 30.0
    assert timeout["write"] == 30.0


# --------------------------------------------------------------------------- #
# 3. Retry budget for uploads
# --------------------------------------------------------------------------- #


class _FailThenSucceed:
    def __init__(self, fail_times: int):
        self.calls = 0
        self.fail_times = fail_times

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise QboServiceUnavailableError(
                "QBO 503 unavailable",
                http_status=503,
                retry_after_seconds=None,
            )
        return {"ok": True}


def _deterministic_clock(monkeypatch, ceiling_seconds):
    """Advance the fake monotonic clock by `ceiling_seconds` per call -- mirrors
    tests/test_qbo_retry_budget.py's `_deterministic_every_attempt_full_timeout_clock`."""
    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        return 1000.0 + (calls["n"] - 1) * ceiling_seconds

    monkeypatch.setattr(retry_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(retry_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(retry_module.random, "uniform", lambda _a, _b: 0.01)
    return calls


@pytest.fixture
def deterministic_retry(monkeypatch):
    return _deterministic_clock(monkeypatch, TIER_C_REQUEST_CEILING_SECONDS)


def test_for_uploads_single_is_at_most_once():
    policy = RetryPolicy.for_uploads_single()
    assert policy.max_attempts == 1
    assert policy.max_total_budget_seconds >= TIER_C_REQUEST_CEILING_SECONDS


def test_for_writes_survives_full_tier_a_timeout_then_succeeds(monkeypatch):
    """
    Regression guard for U-233: a for_writes() call that fails once, after
    consuming a full realistic tier-A timeout, must still get a second
    attempt and succeed -- under the OLD flat 30s budget this scenario
    would have raised after exactly 1 call (budget spent entirely on the
    first timed-out attempt). Uses its own local clock (not the shared
    `deterministic_retry` fixture, which models tier C) because for_writes()
    defaults to tier A.
    """
    _deterministic_clock(monkeypatch, TIER_A_REQUEST_CEILING_SECONDS)

    op = _FailThenSucceed(fail_times=1)
    result = execute_with_retry(op, RetryPolicy.for_writes(), operation_name="qbo.attachable.upload")
    assert result == {"ok": True}
    assert op.calls == 2


# --------------------------------------------------------------------------- #
# 4. At-most-once upload (no retry on ambiguous create)
# --------------------------------------------------------------------------- #


def _upload_client(monkeypatch, handler):
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    shared = _auth_client(client)
    return QboAttachableClient(realm_id=REALM_ID, http_client=shared)


def _call_upload(attachable):
    attachable.upload_attachable(
        file_content=b"%PDF-1.4 payload",
        filename="a.pdf",
        content_type="application/pdf",
        entity_type="Bill",
        entity_id="123",
    )


@pytest.mark.parametrize(
    "failure_mode,expected_exc",
    [
        ("timeout", QboTimeoutError),
        ("503", QboServiceUnavailableError),
        ("malformed_2xx", QboMalformedResponseError),
    ],
)
def test_upload_retryable_error_sends_exactly_one_post(
    monkeypatch, deterministic_retry, failure_mode, expected_exc
):
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if failure_mode == "timeout":
            raise httpx.ReadTimeout("timed out", request=httpx.Request("POST", "https://qbo"))
        if failure_mode == "503":
            return httpx.Response(503, json={"Fault": {"Error": [{"Message": "busy"}]}})
        return httpx.Response(200, text="not-json")

    attachable = _upload_client(monkeypatch, handler)
    with pytest.raises(expected_exc):
        _call_upload(attachable)
    assert calls["n"] == 1


def test_upload_does_not_retry_on_503(monkeypatch, deterministic_retry):
    bodies = []
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        attempts["n"] += 1
        return httpx.Response(503, json={"Fault": {"Error": [{"Message": "busy"}]}})

    attachable = _upload_client(monkeypatch, handler)
    payload = b"%PDF-1.4 " + b"x" * 4096

    with pytest.raises(QboServiceUnavailableError):
        attachable.upload_attachable(
            file_content=payload,
            filename="big.pdf",
            content_type="application/pdf",
            entity_type="Bill",
            entity_id="123",
        )

    assert attempts["n"] == 1
    assert len(bodies) == 1
    assert payload in bodies[0]


# --------------------------------------------------------------------------- #
# 5. Write gate
# --------------------------------------------------------------------------- #


def test_upload_write_gate_refuses_without_transport(monkeypatch):
    monkeypatch.delenv("ALLOW_QBO_WRITES", raising=False)
    http = MagicMock()
    shared = _auth_client(http)
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)

    with pytest.raises(QboWriteRefusedError):
        attachable.upload_attachable(
            file_content=b"pdf",
            filename="a.pdf",
            content_type="application/pdf",
            entity_type="Bill",
            entity_id="123",
        )
    http.request.assert_not_called()


# --------------------------------------------------------------------------- #
# 6. E5 propagation through BillService.push_to_qbo
# --------------------------------------------------------------------------- #


def test_write_refused_from_attachment_leg_escapes_push_to_qbo():
    bill = MagicMock()
    bill.public_id = "11111111-1111-1111-1111-111111111111"
    bill.id = 1

    qbo_bill = MagicMock()
    qbo_bill.qbo_id = "qbo-bill-1"

    line_item = MagicMock()
    line_item.public_id = "22222222-2222-2222-2222-222222222222"

    attachment_link = MagicMock()
    attachment_link.attachment_id = 99

    attachment = MagicMock()
    attachment.id = 99
    attachment.blob_url = "https://blob.example/file"

    service = BillService.__new__(BillService)
    service._qbo_bill_connector = MagicMock()
    service._qbo_bill_connector.sync_to_qbo_bill.return_value = qbo_bill
    service.bill_line_item_service = MagicMock()
    service.bill_line_item_service.read_by_bill_id.return_value = [line_item]
    service.bill_line_item_attachment_service = MagicMock()
    service.bill_line_item_attachment_service.read_by_bill_line_item_id.return_value = attachment_link
    service.attachment_service = MagicMock()
    service.attachment_service.read_by_id.return_value = attachment
    connector = MagicMock()
    connector.sync_attachment_to_qbo.side_effect = QboWriteRefusedError(
        "ALLOW_QBO_WRITES is not true"
    )
    service._attachable_attachment_connector = connector

    with pytest.raises(QboWriteRefusedError):
        service.push_to_qbo(bill=bill, realm_id=REALM_ID)


# --------------------------------------------------------------------------- #
# 8. requestid default off for upload
# --------------------------------------------------------------------------- #


def test_upload_omits_requestid(monkeypatch):
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_upload_success_response())

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    shared = _auth_client(client)
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)
    attachable.upload_attachable(
        file_content=b"pdf",
        filename="a.pdf",
        content_type="application/pdf",
        entity_type="Bill",
        entity_id="123",
    )

    assert "requestid" not in captured["url"]


# --------------------------------------------------------------------------- #
# 9. Error-mapping contract (shared client semantics)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status,expected_cls,expected_retryable",
    [
        (500, QboServerError, True),
        (503, QboServiceUnavailableError, True),
        (429, QboRateLimitError, True),
    ],
)
def test_attachable_get_http_error_mapping(
    status, expected_cls, expected_retryable, monkeypatch, deterministic_retry
):
    monkeypatch.setenv("ALLOW_QBO_WRITES", "true")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"Fault": {"Error": [{"Message": "err"}]}})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    shared = _auth_client(client)
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)

    with pytest.raises(expected_cls) as exc_info:
        attachable.get_attachable("att-1")
    assert exc_info.value.is_retryable is expected_retryable


def test_attachable_get_timeout_maps_retryable(monkeypatch, deterministic_retry):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=httpx.Request("GET", "https://qbo"))

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, timeout=_TIMEOUT_TIERS["A"])
    shared = _auth_client(client)
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)

    with pytest.raises(QboTimeoutError) as exc_info:
        attachable.get_attachable("att-1")
    assert exc_info.value.is_retryable is True


def test_attachable_get_transport_error_maps_retryable(monkeypatch, deterministic_retry):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=httpx.Request("GET", "https://qbo"))

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    shared = _auth_client(client)
    attachable = QboAttachableClient(realm_id=REALM_ID, http_client=shared)

    with pytest.raises(QboTransportError) as exc_info:
        attachable.get_attachable("att-1")
    assert exc_info.value.is_retryable is True
