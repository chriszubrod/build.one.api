"""U-762: the emailed bill apply-reviewer-decision route takes a typed body.

`POST /bill/{public_id}/apply-reviewer-decision` used to take `body: dict` and
read fields with `.get()`, so a malformed or missing-field body reached the
service. It now takes `BillApplyReviewerDecisionRequest`, which shares ONE
base model (`EmailedReviewerDecisionRequestBase`) with the expense route's
`ExpenseApplyReviewerDecisionRequest`. These tests pin:

  * model parity (same fields / types / defaults / descriptions / config);
  * malformed bodies -> FastAPI 422 on the bill route, before the service runs,
    with the SAME response the expense route gives for the same body;
  * unknown extra fields -> handled exactly as the expense route does;
  * a well-formed body -> the service gets exactly the kwargs the old
    `body.get(...)` code passed.

Everything is mocked: no DB, no network.
"""

import importlib
import inspect
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.bill.api.schemas import BillApplyReviewerDecisionRequest
from entities.expense.api.schemas import ExpenseApplyReviewerDecisionRequest
from entities.review.api.schemas import EmailedReviewerDecisionRequestBase

PID = "PID-1"
FIELDS = (
    "decision",
    "reviewer_email",
    "sub_cost_code_public_id",
    "description",
    "raw_reply_text",
    "reviewer_email_message_public_id",
)
VALID = {
    "decision": "approved",
    "reviewer_email": "pm@example.com",
    "sub_cost_code_public_id": "SCC",
    "description": "d",
    "raw_reply_text": "t",
    "reviewer_email_message_public_id": "MSG",
}
ROUTES = {
    "bill": dict(router="entities.bill.api.router", svc="BillService", pid_kw="bill_public_id", base="/api/v1/bill"),
    "expense": dict(
        router="entities.expense.api.router", svc="ExpenseService", pid_kw="expense_public_id", base="/api/v1/expense"
    ),
}


def _old_get_kwargs(body: dict) -> dict:
    """Fixture of the pre-U-762 bill route: `body: dict` + `.get()` per field."""
    return {
        "bill_public_id": PID,
        "decision": body.get("decision"),
        "reviewer_email": body.get("reviewer_email"),
        "sub_cost_code_public_id": body.get("sub_cost_code_public_id"),
        "description": body.get("description"),
        "raw_reply_text": body.get("raw_reply_text"),
        "reviewer_email_message_public_id": body.get("reviewer_email_message_public_id"),
    }


def _post(entity, json_body):
    r = ROUTES[entity]
    router_mod = importlib.import_module(r["router"])
    dep = inspect.signature(router_mod.apply_reviewer_decision_router).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub", "id": 77}
    try:
        with patch.object(router_mod, r["svc"]) as svc:
            m = svc.return_value.apply_reviewer_decision
            m.return_value = {"decision_applied": "approved"}
            resp = TestClient(app, raise_server_exceptions=False).post(
                f"{r['base']}/{PID}/apply-reviewer-decision", json=json_body
            )
            return resp, m
    finally:
        app.dependency_overrides.pop(dep, None)


def _without(key):
    return {k: v for k, v in VALID.items() if k != key}


# --------------------------------------------------------------------------- parity


def test_both_entity_models_subclass_the_shared_base():
    assert issubclass(BillApplyReviewerDecisionRequest, EmailedReviewerDecisionRequestBase)
    assert issubclass(ExpenseApplyReviewerDecisionRequest, EmailedReviewerDecisionRequestBase)


def test_bill_and_expense_models_have_identical_fields():
    b = BillApplyReviewerDecisionRequest.model_fields
    e = ExpenseApplyReviewerDecisionRequest.model_fields
    assert tuple(b) == tuple(e) == FIELDS
    for name in FIELDS:
        fb, fe = b[name], e[name]
        assert fb.annotation == fe.annotation, name
        assert fb.is_required() == fe.is_required(), name
        assert fb.default == fe.default, name
        assert fb.description == fe.description, name
    assert BillApplyReviewerDecisionRequest.model_config == ExpenseApplyReviewerDecisionRequest.model_config
    required = {n for n in FIELDS if b[n].is_required()}
    assert required == {"decision", "reviewer_email", "reviewer_email_message_public_id"}


# --------------------------------------------------------------------------- malformed -> 422

MALFORMED = {
    "missing_decision": _without("decision"),
    "missing_reviewer_email": _without("reviewer_email"),
    "missing_message_public_id": _without("reviewer_email_message_public_id"),
    "null_decision": {**VALID, "decision": None},
    "decision_int": {**VALID, "decision": 123},
    "reviewer_email_int": {**VALID, "reviewer_email": 5},
    "scc_list": {**VALID, "sub_cost_code_public_id": ["SCC"]},
    "description_dict": {**VALID, "description": {"x": 1}},
    "raw_reply_text_list": {**VALID, "raw_reply_text": ["t"]},
    "empty_object": {},
    "json_array": [VALID],
    "json_string": "approved",
}


@pytest.mark.parametrize("case", list(MALFORMED))
def test_malformed_bill_body_is_422_before_the_service(case):
    resp, m = _post("bill", MALFORMED[case])
    assert resp.status_code == 422, resp.text
    m.assert_not_called()


@pytest.mark.parametrize("case", list(MALFORMED))
def test_malformed_body_response_matches_expense_route(case):
    b_resp, b_m = _post("bill", MALFORMED[case])
    e_resp, e_m = _post("expense", MALFORMED[case])
    assert (b_resp.status_code, b_resp.json()) == (e_resp.status_code, e_resp.json())
    b_m.assert_not_called()
    e_m.assert_not_called()


# --------------------------------------------------------------------------- extra fields


def test_unknown_extra_fields_handled_like_expense():
    body = {**VALID, "unexpected": "x", "is_draft": True}
    b_resp, b_m = _post("bill", body)
    e_resp, e_m = _post("expense", body)
    assert b_resp.status_code == e_resp.status_code == 200
    # Ignored (pydantic default `extra='ignore'`): never forwarded to the service.
    assert b_m.call_args.kwargs == _old_get_kwargs(VALID)
    assert "unexpected" not in e_m.call_args.kwargs


# --------------------------------------------------------------------------- well-formed: unchanged


@pytest.mark.parametrize(
    "body",
    [
        VALID,
        {**VALID, "decision": "rejected", "sub_cost_code_public_id": None},
        _without("description") | {"raw_reply_text": None},
        {k: VALID[k] for k in ("decision", "reviewer_email", "reviewer_email_message_public_id")},
    ],
    ids=["full", "rejected_null_scc", "optional_omitted_or_null", "required_only"],
)
def test_valid_body_passes_same_kwargs_as_old_get_code(body):
    resp, m = _post("bill", body)
    assert resp.status_code == 200, resp.text
    m.assert_called_once()
    assert m.call_args.args == ()
    assert m.call_args.kwargs == _old_get_kwargs(body)


def test_bill_route_declares_the_typed_model_in_openapi():
    op = app.openapi()["paths"]["/api/v1/bill/{public_id}/apply-reviewer-decision"]["post"]
    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/BillApplyReviewerDecisionRequest")
    assert op["requestBody"]["required"] is True
