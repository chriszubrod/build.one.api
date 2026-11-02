"""U-551b — the Expense transactional decide refuses the suspense code (00.02), like Bill (U-632)."""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.expense.business.service import ExpenseService
from entities.review.api.router import apply_review_decision_expense_router
from shared.api.errors import ErrorCode
from shared.lifecycle.completion_gate import HoldingCodeRefused

EXPENSE_PID = "expense-pub-1"
CALLER_USER_ID = 42
SCC_PID = "scc-holding-pub"
IDEM_KEY = "22222222-2222-4222-8222-222222222222"
APPLY_PATH = f"/api/v1/apply/review-decision/expense/{EXPENSE_PID}"
HOLDING_MESSAGE = (
    "This expense cannot be approved while coded to the suspense "
    "sub cost code. Recode those lines first."
)


def _decide(svc: ExpenseService, *, decision: str = "approved"):
    return svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision=decision,
        sub_cost_code_public_id=SCC_PID,
        description=None,
        idempotency_key=IDEM_KEY,
        line_row_version="rv",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )


def _stub_service() -> ExpenseService:
    svc = ExpenseService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=7, public_id=EXPENSE_PID, is_draft=True)
    )
    return svc


def _run_with_scc(scc):
    """Run the decide with one sole line and the given resolved sub cost code."""
    svc = _stub_service()
    line = SimpleNamespace(id=501, row_version="rv", sub_cost_code_id=2)
    eli = MagicMock()
    eli.read_by_expense_id.return_value = [line]
    review_repo = MagicMock()
    review_repo.read_by_idempotency_key.return_value = None
    get_conn = MagicMock(name="get_connection")
    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        return_value=eli,
    ), patch(
        "entities.review.business.recipient_service.ReviewRecipientService"
    ) as recipients, patch(
        "entities.review.persistence.repo.ReviewRepository", return_value=review_repo
    ), patch(
        "entities.expense.business.service.get_connection", get_conn
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        return_value=scc,
    ):
        recipients.return_value.resolve_for_expense.return_value = {
            "to": [SimpleNamespace(email="pm@x.com", user_id=CALLER_USER_ID)],
            "cc": [],
        }
        try:
            return svc, eli, review_repo, get_conn, _decide(svc)
        except HoldingCodeRefused as exc:
            return svc, eli, review_repo, get_conn, exc


def test_approve_on_holding_code_refused_before_any_write():
    _svc, eli, review_repo, get_conn, outcome = _run_with_scc(
        SimpleNamespace(id=99, is_holding_code=True)
    )
    assert isinstance(outcome, HoldingCodeRefused)
    assert outcome.status_code == 422
    assert outcome.error_code == ErrorCode.HOLDING_CODE
    assert str(outcome.detail) == HOLDING_MESSAGE
    # Refused before the transaction even opens: no lock, no recode, no Review.
    get_conn.assert_not_called()
    eli.update_coding_in_transaction.assert_not_called()
    review_repo.create.assert_not_called()


def test_approve_on_ordinary_code_is_not_refused():
    """The predicate is the holding flag, not 'any code': a normal SCC gets past it."""
    from shared.database import DatabaseConstraintError  # noqa: F401  (import sanity)

    svc = _stub_service()
    scc = SimpleNamespace(id=10, is_holding_code=False)
    line = SimpleNamespace(id=501, row_version="rv", sub_cost_code_id=2)
    eli = MagicMock()
    eli.read_by_expense_id.return_value = [line]
    review_repo = MagicMock()
    review_repo.read_by_idempotency_key.return_value = None
    review_repo.read_current_by_expense_id_for_update.return_value = None
    review_repo.create.return_value = SimpleNamespace(
        public_id="rev-1",
        review_status_id=30,
        user_id=CALLER_USER_ID,
        review_kind="approved",
        expense_id=7,
    )
    eli.count_by_expense_id_for_update.return_value = 1
    eli.read_by_expense_id_in_transaction.return_value = [line]
    get_conn = MagicMock(name="get_connection")
    get_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
    get_conn.return_value.__exit__ = MagicMock(return_value=False)
    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        return_value=eli,
    ), patch(
        "entities.review.business.recipient_service.ReviewRecipientService"
    ) as recipients, patch(
        "entities.review.persistence.repo.ReviewRepository", return_value=review_repo
    ), patch(
        "entities.review_status.business.service.ReviewStatusService"
    ) as rs, patch(
        "entities.expense.business.service.get_connection", get_conn
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        return_value=scc,
    ):
        recipients.return_value.resolve_for_expense.return_value = {
            "to": [SimpleNamespace(email="pm@x.com", user_id=CALLER_USER_ID)],
            "cc": [],
        }
        rs.return_value.get_approved_status.return_value = SimpleNamespace(
            id=30, name="Approved"
        )
        result = _decide(svc)
    assert result["decision_applied"] == "approved"
    eli.update_coding_in_transaction.assert_called_once()
    review_repo.create.assert_called_once()


def test_reject_does_not_consult_the_holding_code():
    """A decline carries no recode, so the suspense code is not in play."""
    svc = _stub_service()
    line = SimpleNamespace(id=501, row_version="rv", sub_cost_code_id=2)
    eli = MagicMock()
    eli.read_by_expense_id.return_value = [line]
    eli.count_by_expense_id_for_update.return_value = 1
    eli.read_by_expense_id_in_transaction.return_value = [line]
    review_repo = MagicMock()
    review_repo.read_by_idempotency_key.return_value = None
    review_repo.read_current_by_expense_id_for_update.return_value = None
    review_repo.create.return_value = SimpleNamespace(
        public_id="rev-2",
        review_status_id=100,
        user_id=CALLER_USER_ID,
        review_kind="declined",
        expense_id=7,
    )
    get_conn = MagicMock(name="get_connection")
    get_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
    get_conn.return_value.__exit__ = MagicMock(return_value=False)
    scc_read = MagicMock()
    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        return_value=eli,
    ), patch(
        "entities.review.business.recipient_service.ReviewRecipientService"
    ) as recipients, patch(
        "entities.review.persistence.repo.ReviewRepository", return_value=review_repo
    ), patch(
        "entities.review_status.business.service.ReviewStatusService"
    ) as rs, patch(
        "entities.expense.business.service.get_connection", get_conn
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        scc_read,
    ):
        recipients.return_value.resolve_for_expense.return_value = {
            "to": [SimpleNamespace(email="pm@x.com", user_id=CALLER_USER_ID)],
            "cc": [],
        }
        rs.return_value.get_declined_statuses.return_value = [
            SimpleNamespace(id=100, name="Declined")
        ]
        result = _decide(svc, decision="rejected")
    assert result["decision_applied"] == "rejected"
    scc_read.assert_not_called()
    eli.update_coding_in_transaction.assert_not_called()


def test_route_answers_422_holding_code_and_writes_nothing():
    """HTTP: same status/error_code as the Bill route's U-632 refusal."""
    dep = inspect.signature(apply_review_decision_expense_router).parameters[
        "current_user"
    ].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub"}
    try:
        with patch(
            "entities.review.api.router.resolve_user_id", return_value=CALLER_USER_ID
        ), patch("entities.review.api.router.ExpenseService") as expense_svc:
            expense_svc.return_value.apply_transactional_reviewer_decision.side_effect = (
                HoldingCodeRefused(HOLDING_MESSAGE)
            )
            response = TestClient(app, raise_server_exceptions=False).post(
                APPLY_PATH,
                json={
                    "decision": "approved",
                    "sub_cost_code_public_id": SCC_PID,
                    "description": None,
                    "idempotency_key": IDEM_KEY,
                    "line_row_version": "rv",
                    "expected_review_public_id": None,
                },
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == ErrorCode.HOLDING_CODE
    assert body["detail"] == HOLDING_MESSAGE


def test_route_holding_arm_is_explicit_and_precedes_value_error():
    """The shared helper re-raises HoldingCodeRefused before any generic mapping."""
    from entities.review.api import router as review_router_module

    src = inspect.getsource(review_router_module._apply_transactional_review_decision)
    assert src.index("except HoldingCodeRefused") < src.index("except ValueError")
