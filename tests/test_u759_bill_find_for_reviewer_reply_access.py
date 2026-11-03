"""U-759 — Bill find-by-conversation-id gates the matched Bill on project access.

Twin of U-565 (Expense). `GET /bill/find-by-conversation-id` used to call the
repository directly, so any BILLS reader could resolve a Bill on a project they
are not assigned to. The route now goes through `BillService.find_for_reviewer_reply`,
which runs `assert_can_access_bill` on the match and answers an inaccessible match
exactly like a miss (null).

Pure-logic: repo, access check and DB are mocked. No live DB.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import MagicMock, patch

import pytest

from shared.access import EntityNotAccessibleError
from shared.api.responses import item_response
from shared.authz.context import system_authz
from shared.rbac_constants import Modules
from tests.test_review_route_rbac import _assert_current_user_rbac

FIND_ROUTE = "/api/v1/get/bill/find-by-conversation-id"
PUBLIC_ID_ROUTE = "/api/v1/get/bill/{public_id}"

SLIM = {
    "id": 4242,
    "public_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "bill_number": "INV-SECRET-206640",
    "total_amount": 9876.54,
    "is_draft": True,
    "created_datetime": "2026-10-01 12:00:00",
    "vendor_name": "Hidden Vendor LLC",
    "source_email_message_id": 9001,
    "conversation_id": "conv-strict",
    "match_kind": "conversation",
}


def _service(repo_result):
    """A BillService with only the repo it needs — bypasses the constructor's
    live wiring; the method under test reads `self.repo` alone."""
    from entities.bill.business.service import BillService

    svc = BillService.__new__(BillService)
    svc.repo = MagicMock()
    svc.repo.find_for_reviewer_reply.return_value = repo_result
    return svc


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def test_accessible_match_returns_row_unchanged_and_passes_exact_kwargs():
    row = dict(SLIM)
    svc = _service(row)
    with patch("entities.bill.business.service.assert_can_access_bill") as gate:
        out = svc.find_for_reviewer_reply(
            conversation_id="conv-strict", bill_number_hint="206640", project_hint="Buffalo"
        )
    assert out is row
    assert out == SLIM
    svc.repo.find_for_reviewer_reply.assert_called_once_with(
        conversation_id="conv-strict", bill_number_hint="206640", project_hint="Buffalo"
    )
    gate.assert_called_once_with(4242)


def test_inaccessible_match_answers_as_no_match_and_logs_id_only(caplog):
    """Inaccessible ≡ not found, so the fuzzy hints are not an existence oracle for
    drafts outside the caller's projects. The warning carries the id and no document data."""
    svc = _service(dict(SLIM))
    with caplog.at_level(logging.WARNING, logger="entities.bill.business.service"), patch(
        "entities.bill.business.service.assert_can_access_bill",
        side_effect=EntityNotAccessibleError("Bill", 4242),
    ) as gate:
        out = svc.find_for_reviewer_reply(
            conversation_id="conv-strict", bill_number_hint="INV-SECRET-206640", project_hint="Buffalo"
        )
    assert out is None
    gate.assert_called_once_with(4242)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "entities.bill.business.service"]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "4242" in msg
    for field in ("public_id", "bill_number", "total_amount", "vendor_name", "conversation_id", "source_email_message_id"):
        value = str(SLIM[field])
        assert value not in msg, f"warning leaked {field}={value!r}"
    assert "Buffalo" not in msg


def test_miss_returns_none_without_access_check():
    with patch("entities.bill.business.service.assert_can_access_bill") as gate:
        assert _service(None).find_for_reviewer_reply(conversation_id="missing") is None
    gate.assert_not_called()


def test_system_admin_bypass_returns_row_without_udf_check():
    """Drain/system callers bypass via system_authz(); the UDF query must not run."""
    svc = _service(dict(SLIM))
    with system_authz(), patch(
        "shared.access._check", side_effect=AssertionError("UDF must not run under bypass")
    ) as check:
        out = svc.find_for_reviewer_reply(conversation_id="conv-strict")
    assert out == SLIM
    check.assert_not_called()


def test_non_admin_real_gate_denies_via_udf():
    """End to end through the real assert_can_access_bill: a non-admin whose UDF says
    no gets None (not an exception, not the row)."""
    from shared.authz.context import clear_authz_context, set_authz_context

    svc = _service(dict(SLIM))
    set_authz_context(user_id=55, company_id=1, is_system_admin=False)
    try:
        with patch("shared.access._check", return_value=False) as check:
            out = svc.find_for_reviewer_reply(conversation_id="conv-strict")
    finally:
        clear_authz_context()
    assert out is None
    check.assert_called_once_with("UserCanAccessBill", 4242)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _routes():
    import entities.bill.api.router as bill_router

    return bill_router.router.routes


def test_route_keeps_bills_read_gate_and_order():
    endpoint = next((r.endpoint for r in _routes() if r.path == FIND_ROUTE and "GET" in r.methods), None)
    assert endpoint is not None, f"no GET route at {FIND_ROUTE}"
    _assert_current_user_rbac(endpoint, Modules.BILLS, "can_read")
    paths = [r.path for r in _routes()]
    assert paths.index(FIND_ROUTE) < paths.index(PUBLIC_ID_ROUTE)


def test_router_delegates_to_service_not_repository():
    from entities.bill.api.router import find_bill_by_conversation_id_router

    with patch("entities.bill.api.router.BillService") as Svc, patch(
        "entities.bill.api.router.BillRepository"
    ) as Repo:
        Svc.return_value.find_for_reviewer_reply.return_value = SLIM
        out = find_bill_by_conversation_id_router(
            conversation_id="conv-abc",
            bill_number_hint="B-1",
            project_hint="Site A",
            current_user={"sub": "user"},
        )
    Svc.return_value.find_for_reviewer_reply.assert_called_once_with(
        conversation_id="conv-abc", bill_number_hint="B-1", project_hint="Site A"
    )
    Repo.assert_not_called()
    assert out == item_response(SLIM)


@pytest.fixture
def bill_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from entities.bill.api.router import find_bill_by_conversation_id_router, router as bill_router
    from shared.api.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(bill_router)
    dep = inspect.signature(find_bill_by_conversation_id_router).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"id": 55, "tenant_id": 1, "sub": "pm"}
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("repo_row", [dict(SLIM), None], ids=["inaccessible_match", "miss"])
def test_http_inaccessible_match_is_indistinguishable_from_miss(bill_client, repo_row):
    svc = _service(repo_row)
    with patch("entities.bill.api.router.BillService", return_value=svc), patch(
        "entities.bill.api.router.BillRepository"
    ) as Repo, patch(
        "entities.bill.business.service.assert_can_access_bill",
        side_effect=EntityNotAccessibleError("Bill", 4242),
    ):
        resp = bill_client.get(
            FIND_ROUTE,
            params={"conversation_id": "conv-strict", "bill_number_hint": "206640", "project_hint": "Buffalo"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"data": None}
    Repo.assert_not_called()
    svc.repo.find_for_reviewer_reply.assert_called_once_with(
        conversation_id="conv-strict", bill_number_hint="206640", project_hint="Buffalo"
    )
