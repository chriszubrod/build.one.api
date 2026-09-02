"""HTTP error bodies carry an additive, sibling `error_code` (U-357d).

`detail` text and status codes are frozen (the iOS offline client substring-matches
them); only the sibling key is new. `ErrorCode` is the wire contract.
"""

import logging

import pytest
from fastapi import Body, FastAPI, HTTPException
from fastapi.testclient import TestClient

from shared.access import EntityNotAccessibleError
from shared.api.errors import ApiError, ErrorCode, install_error_handlers
from shared.api.responses import raise_database_error, raise_not_found, raise_workflow_error
from shared.database import DatabaseConstraintError
from shared.db_constraints import FK_REFERENCE_VIOLATION, UNIQUE_VIOLATION

# Frozen wire contract — the iOS TimeEntryService classifiers and the web client key
# on these exact literals. Renaming one is a deliberate wire change, not a refactor.
WIRE_CODES = {
    "ENTRY_LOCKED": "entry_locked",
    "TRANSITION_INVALID": "transition_invalid",
    "DUPLICATE_KEY": "duplicate_key",
    "FK_VIOLATION": "fk_violation",
    "NOT_FOUND": "not_found",
    "VALIDATION_ERROR": "validation_error",
}


def test_error_code_literals_are_the_wire_contract():
    assert {name: getattr(ErrorCode, name) for name in WIRE_CODES} == WIRE_CODES


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/http-exception")
    def _http_exception():
        raise HTTPException(status_code=422, detail="x")

    @app.get("/api-error")
    def _api_error():
        raise ApiError(
            status_code=422,
            detail="x",
            error_code=ErrorCode.ENTRY_LOCKED,
            headers={"X-Test": "1"},
        )

    @app.get("/list-detail")
    def _list_detail():
        raise HTTPException(status_code=422, detail=[{"msg": "bad", "type": "value_error"}])

    @app.get("/no-body")
    def _no_body():
        raise HTTPException(status_code=304)

    @app.get("/not-accessible")
    def _not_accessible():
        raise EntityNotAccessibleError("Bill", 1)

    @app.post("/validated")
    def _validated(x: int = Body(...)):
        return {"x": x}

    return TestClient(app)


@pytest.mark.parametrize(
    "method, path, status_code, body",
    [
        ("get", "/http-exception", 422, {"detail": "x", "error_code": None}),
        ("get", "/api-error", 422, {"detail": "x", "error_code": "entry_locked"}),
        ("get", "/list-detail", 422, {"detail": [{"msg": "bad", "type": "value_error"}], "error_code": None}),
        # Starlette raises these itself — the handler is registered on the base class.
        ("get", "/missing", 404, {"detail": "Not Found", "error_code": None}),
        ("post", "/http-exception", 405, {"detail": "Method Not Allowed", "error_code": None}),
        # Per-row access masker: 404 (not 403), detail unchanged, code added.
        ("get", "/not-accessible", 404, {"detail": "Not found", "error_code": "not_found"}),
    ],
)
def test_error_body_shape(client, method, path, status_code, body):
    response = getattr(client, method)(path)
    assert response.status_code == status_code
    assert response.json() == body


def test_request_validation_keeps_fastapi_detail_and_adds_code(client):
    # FastAPI's own 422 shape (a list of error dicts) is preserved verbatim; only the sibling is new.
    response = client.post("/validated", json="bad")
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"] == ["body"]
    assert body["error_code"] == "validation_error"


def test_api_error_headers_pass_through(client):
    assert client.get("/api-error").headers.get("X-Test") == "1"


def test_no_body_status_emits_no_json_body(client):
    response = client.get("/no-body")
    assert response.status_code == 304
    assert response.content == b""


def test_real_app_wiring(caplog):
    # The real app (conftest blocks live DB connects at import): proves the handlers
    # AND the client-build read are wired, rather than grepping app.py's source.
    from app import app

    real = TestClient(app)
    response = real.get("/api/v1/__u357d_probe__")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found", "error_code": None}

    with caplog.at_level(logging.DEBUG, logger="app"):
        real.get("/api/v1/__u357d_probe__", headers={"X-Client-Build": "1.2.3 (45)"})
    record = next(r for r in caplog.records if r.getMessage() == "client.build")
    assert record.client_build == "1.2.3 (45)"
    assert record.path == "/api/v1/__u357d_probe__"


@pytest.mark.parametrize(
    "err, expected_status, expected_code",
    [
        ("Cannot transition from 'submitted' to 'draft'", 400, "transition_invalid"),
        (
            "Cannot update time entry in 'submitted' status. Only 'draft' entries can be edited.",
            400,
            "entry_locked",
        ),
        ("row-version mismatch", 409, None),  # 409 branches never derive a code
        ("something else", 400, None),
    ],
)
def test_raise_workflow_error_classification(err, expected_status, expected_code):
    with pytest.raises(ApiError) as exc_info:
        raise_workflow_error(err, "default")
    exc = exc_info.value
    assert (exc.status_code, exc.detail, exc.error_code) == (expected_status, err, expected_code)


def test_raise_workflow_error_explicit_error_code_wins():
    with pytest.raises(ApiError) as exc_info:
        raise_workflow_error("Cannot transition from 'submitted' to 'draft'", "default", error_code="custom_code")
    assert (exc_info.value.status_code, exc_info.value.error_code) == (400, "custom_code")


def test_raise_not_found_carries_code():
    with pytest.raises(ApiError) as exc_info:
        raise_not_found("Bill")
    exc = exc_info.value
    assert (exc.status_code, exc.detail, exc.error_code) == (404, "Bill not found", "not_found")


_UNIQUE_2627 = (
    "Violation of UNIQUE KEY constraint 'UQ_RoleModule_RoleId_ModuleId'. "
    "Cannot insert duplicate key in object 'dbo.RoleModule'. "
    "The duplicate key value is (1, 2). (2627)"
)
_FK_547 = (
    "The DELETE statement conflicted with the REFERENCE constraint "
    "\"FK_TimeLog_Project\". The conflict occurred in database \"buildone\". (547)"
)


@pytest.mark.parametrize(
    "violation, original, expected_detail, expected_code",
    [
        # UNIQUE keeps the ORIGINAL driver message (the iOS duplicate-claim matcher keys on it).
        (UNIQUE_VIOLATION, _UNIQUE_2627, _UNIQUE_2627, "duplicate_key"),
        # FK surfaces the clean schema-free message.
        (FK_REFERENCE_VIOLATION, _FK_547, FK_REFERENCE_VIOLATION.message, "fk_violation"),
    ],
)
def test_raise_database_error_classified_paths(violation, original, expected_detail, expected_code):
    with pytest.raises(ApiError) as exc_info:
        raise_database_error(DatabaseConstraintError(violation, original))
    exc = exc_info.value
    assert (exc.status_code, exc.detail, exc.error_code) == (422, expected_detail, expected_code)
