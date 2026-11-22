"""U-797c4b item 4 — BillCredit reviewer-decision routes and request schema (fakes only)."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from entities.bill_credit.api import router as bc_router
from entities.bill_credit.api.schemas import BillCreditApplyReviewerDecisionRequest
from entities.bill_credit.business.service import BillCreditService
from entities.review.api.schemas import EmailedReviewerDecisionRequestBase
from entities.review.business.decision_errors import ReviewDecisionApplyError


def _route(path: str, method: str):
    for r in bc_router.router.routes:
        if r.path == path and method in r.methods:
            return r
    raise AssertionError(f"no {method} {path}")


APPLY = "/api/v1/bill-credit/{public_id}/apply-reviewer-decision"
SYNC = "/api/v1/bill-credit/{public_id}/review-replies/sync"


def test_both_routes_are_registered_as_post():
    assert _route(APPLY, "POST").endpoint is bc_router.apply_reviewer_decision_router
    assert _route(SYNC, "POST").endpoint is bc_router.sync_review_replies_router


@pytest.mark.parametrize("fn", ["apply_reviewer_decision_router", "sync_review_replies_router"])
def test_routes_are_gated_by_bill_credits_can_update(fn):
    src = inspect.getsource(getattr(bc_router, fn))
    assert 'require_module_api(Modules.BILL_CREDITS, "can_update")' in src


def test_sync_route_delegates_with_bill_credit_entity_type():
    src = inspect.getsource(bc_router.sync_review_replies_router)
    assert "return sync_review_replies(" in src
    assert 'entity_type="BillCredit"' in src


def test_request_schema_is_the_shared_emailed_base():
    assert issubclass(BillCreditApplyReviewerDecisionRequest, EmailedReviewerDecisionRequestBase)


def test_request_schema_rejects_an_empty_body():
    with pytest.raises(ValidationError):
        BillCreditApplyReviewerDecisionRequest()


def test_sync_returns_404_for_unknown_credit():
    with patch.object(BillCreditService, "read_by_public_id", return_value=None):
        with pytest.raises(HTTPException) as err:
            bc_router.sync_review_replies_router("bc-missing", current_user={})
    assert err.value.status_code == 404


def test_sync_refuses_a_completed_credit():
    parent = MagicMock(is_draft=False)
    with patch.object(BillCreditService, "read_by_public_id", return_value=parent):
        with pytest.raises(Exception) as err:
            bc_router.sync_review_replies_router("bc-1", current_user={})
    assert "draft" in str(getattr(err.value, "detail", err.value))


def test_apply_maps_value_error_to_400():
    body = BillCreditApplyReviewerDecisionRequest(
        decision="approved", reviewer_email="pm@example.test",
        sub_cost_code_public_id="scc-1", reviewer_email_message_public_id="m-1",
    )
    with patch.object(BillCreditService, "apply_reviewer_decision", side_effect=ValueError("no draft")):
        with pytest.raises(HTTPException) as err:
            bc_router.apply_reviewer_decision_router("bc-1", body, current_user={})
    assert err.value.status_code == 400
    assert err.value.detail == "no draft"


def test_apply_maps_engine_refusal_to_its_own_status():
    body = BillCreditApplyReviewerDecisionRequest(
        decision="approved", reviewer_email="pm@example.test",
        sub_cost_code_public_id="scc-1", reviewer_email_message_public_id="m-1",
    )
    refusal = ReviewDecisionApplyError(status_code=409, detail="stale", error_code="line_row_version_stale")
    with patch.object(BillCreditService, "apply_reviewer_decision", side_effect=refusal):
        with pytest.raises(Exception) as err:
            bc_router.apply_reviewer_decision_router("bc-1", body, current_user={})
    assert getattr(err.value, "status_code", None) == 409
    assert getattr(err.value, "error_code", None) == "line_row_version_stale"


def test_apply_passes_the_body_through_to_the_service():
    body = BillCreditApplyReviewerDecisionRequest(
        decision="rejected", reviewer_email="pm@example.test",
        raw_reply_text="needs revision", reviewer_email_message_public_id="m-1",
    )
    with patch.object(BillCreditService, "apply_reviewer_decision", return_value={"ok": 1}) as svc:
        bc_router.apply_reviewer_decision_router("bc-1", body, current_user={})
    kw = svc.call_args.kwargs
    assert kw["bill_credit_public_id"] == "bc-1"
    assert kw["decision"] == "rejected"
    assert kw["raw_reply_text"] == "needs revision"
