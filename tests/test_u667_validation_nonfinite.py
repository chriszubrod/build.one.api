"""U-667: 422 bodies must JSON-encode when Pydantic echoes non-finite inputs."""
from __future__ import annotations

import inspect
from decimal import Decimal
from typing import List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from entities.coding_suggestion.api.bill_router import router as bill_coding_router
from entities.coding_suggestion.api.bill_router import suggest_bill_coding_router
from shared.api.errors import ErrorCode, install_error_handlers

_VENDOR_UUID = "550e8400-e29b-41d4-a716-446655440001"


class _NonFiniteProbeModel(BaseModel):
    optional_dec: Optional[Decimal] = None
    floats: List[float] = []
    required_str: str


@pytest.fixture(scope="module")
def probe_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/probe")
    def probe(body: _NonFiniteProbeModel):
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def _expected_finite_invalid_detail() -> list:
    # FastAPI prefixes body fields in `loc` and omits Pydantic's `url` key from wire JSON.
    return [
        {
            "type": "missing",
            "loc": ["body", "required_str"],
            "msg": "Field required",
            "input": {"optional_dec": "2.5", "floats": [1.0, 2.0]},
        }
    ]


def test_finite_invalid_body_unchanged_422_json(probe_client: TestClient):
    resp = probe_client.post(
        "/probe",
        json={"optional_dec": "2.5", "floats": [1.0, 2.0]},
    )
    assert resp.status_code == 422
    assert resp.json() == {
        "detail": _expected_finite_invalid_detail(),
        "error_code": ErrorCode.VALIDATION_ERROR,
    }


@pytest.mark.parametrize(
    "content",
    [
        '{"optional_dec": 1e400, "floats": [], "required_str": "x"}',
        '{"optional_dec": -1e400, "floats": [], "required_str": "x"}',
        '{"optional_dec": "2.5", "floats": [1e400]}',
        '{"extra_inf": 1e400}',
        "1e400",
    ],
)
def test_non_finite_inputs_return_422_json(probe_client: TestClient, content: str):
    resp = probe_client.post(
        "/probe",
        content=content,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == ErrorCode.VALIDATION_ERROR
    assert isinstance(body["detail"], list)


@pytest.fixture
def bill_coding_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(bill_coding_router)
    rbac_dep = inspect.signature(suggest_bill_coding_router).parameters["_"].default.dependency
    app.dependency_overrides[rbac_dep] = lambda: {"sub": "test-sub"}
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "payload",
    [
        '{"vendor_public_id": "'
        + _VENDOR_UUID
        + '", "amount": 1e400}',
        '{"vendor_public_id": "'
        + _VENDOR_UUID
        + '", "memo": 1e400}',
    ],
)
def test_bill_coding_suggest_non_finite_returns_422(bill_coding_client: TestClient, payload: str):
    with patch("entities.coding_suggestion.api.bill_router.VendorService") as vendor_svc:
        vendor_svc.return_value.read_by_public_id.side_effect = AssertionError(
            "DB must not be reached on validation failure"
        )
        resp = bill_coding_client.post(
            "/api/v1/bill/coding/suggest",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == ErrorCode.VALIDATION_ERROR
    assert isinstance(body["detail"], list)


# --- Round 1 (Codex): carriers the encoder unpacks, NaN/Infinity literals, exact
# sanitized strings, and byte identity for finite bodies.

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import field_validator

from shared.api.errors import error_response


class _CtxCarrierModel(BaseModel):
    amount: float

    @field_validator("amount")
    @classmethod
    def _reject_with_payload(cls, value):
        error = ValueError("invalid amount")
        error.amount = value  # an attribute jsonable_encoder unpacks from ctx
        error.seen = {value}
        raise error


def _app_with(handler=None) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    if handler is not None:
        app.add_exception_handler(RequestValidationError, handler)

    @app.post("/probe")
    def probe(body: _NonFiniteProbeModel):
        return {"ok": True}

    @app.post("/ctx")
    def ctx(body: _CtxCarrierModel):
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


async def _pre_u667_handler(request, exc):
    # The handler exactly as it was before U-667, to pin byte identity.
    return error_response(422, jsonable_encoder(exc.errors()), ErrorCode.VALIDATION_ERROR)


@pytest.mark.parametrize(
    "content,expected_input",
    [
        ('{"amount": 1e400}', "inf"),
        ('{"amount": -1e400}', "-inf"),
        ('{"amount": NaN}', "nan"),
        ('{"amount": Infinity}', "inf"),
        ('{"amount": -Infinity}', "-inf"),
    ],
)
def test_r1_ctx_carried_non_finite_renders_422(content, expected_input):
    resp = _app_with().post("/ctx", content=content, headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["input"] == expected_input


@pytest.mark.parametrize(
    "content,expected_input",
    [
        ('{"optional_dec": 1e400, "floats": [], "required_str": "x"}', "inf"),
        ('{"optional_dec": NaN, "floats": [], "required_str": "x"}', "nan"),
        # float fields accept inf, so omit required_str: the 422 echoes the whole body.
        ('{"optional_dec": "2.5", "floats": [-Infinity]}', {"optional_dec": "2.5", "floats": ["-inf"]}),
    ],
)
def test_r1_sanitized_strings_are_exact(content, expected_input):
    resp = _app_with().post("/probe", content=content, headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["input"] == expected_input


@pytest.mark.parametrize(
    "body",
    [
        {"optional_dec": "2.5", "floats": [1.0, 2.0]},
        {"optional_dec": "abc", "floats": ["x"], "required_str": "y"},
        {"floats": [1.5, "nope"], "required_str": 3},
    ],
)
def test_r1_finite_bodies_are_byte_identical(body):
    new = _app_with().post("/probe", json=body)
    old = _app_with(_pre_u667_handler).post("/probe", json=body)
    assert new.status_code == old.status_code == 422
    assert new.content == old.content


@pytest.mark.parametrize(
    "amount", ['"NaN"', '"Infinity"', '"-Infinity"', '"sNaN"', "NaN", "Infinity", "-Infinity", "1e400"]
)
def test_r1_bill_suggest_amount_non_finite_spellings_render_422(bill_coding_client, amount):
    payload = '{"vendor_public_id": "' + _VENDOR_UUID + '", "amount": ' + amount + "}"
    with patch("entities.coding_suggestion.api.bill_router.VendorService") as vendor_svc:
        vendor_svc.return_value.read_by_public_id.side_effect = AssertionError("no DB on 422")
        resp = bill_coding_client.post(
            "/api/v1/bill/coding/suggest", content=payload, headers={"Content-Type": "application/json"}
        )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == ErrorCode.VALIDATION_ERROR
