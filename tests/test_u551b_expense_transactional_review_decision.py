"""U-551b — transactional POST /apply/review-decision/expense/{id}."""

import inspect
from functools import wraps
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

from app import app
from entities.expense.business.service import ExpenseService
from entities.review.api.router import (
    apply_review_decision_expense_router,
    router as review_router,
)
from entities.review.api.schemas import ExpenseReviewDecisionApplyRequest
from entities.review.business.decision_errors import (
    ReviewDecisionApplyError,
    review_decision_apply_error,
)
from shared.api.errors import ErrorCode
from shared.db_constraints import UNIQUE_VIOLATION
from shared.database import DatabaseConstraintError, DatabaseConcurrencyError, is_transient_error
from shared.lifecycle.terminal_lock import StatusLockedError
from shared.rbac_constants import Modules

EXPENSE_PID = "expense-pub-1"
CALLER_USER_ID = 42
OTHER_USER_ID = 99
SCC_PID = "scc-pub-1"
IDEM_KEY = "11111111-1111-4111-8111-111111111111"
APPLY_PATH = f"/api/v1/apply/review-decision/expense/{EXPENSE_PID}"

_APPLY_PATCH_TARGETS = (
    "entities.expense.business.service.SubCostCodeService",
    "entities.expense_line_item.business.service.ExpenseLineItemService",
    "entities.review_status.business.service.ReviewStatusService",
    "entities.review.persistence.repo.ReviewRepository",
    "entities.expense.business.service.get_connection",
    "entities.review.business.recipient_service.ReviewRecipientService",
)


def _patch_apply_transactional(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    for target in reversed(_APPLY_PATCH_TARGETS):
        wrapper = patch(target)(wrapper)
    return wrapper


def _request_json(**overrides):
    body = {
        "decision": "approved",
        "sub_cost_code_public_id": SCC_PID,
        "description": "Supplies",
        "idempotency_key": IDEM_KEY,
        "line_row_version": "rv-client",
        "expected_review_public_id": None,
    }
    body.update(overrides)
    return body


def _expense_stub(*, expense_id: int = 7):
    svc = ExpenseService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=expense_id,
            public_id=EXPENSE_PID,
            is_draft=True,
        )
    )
    return svc


def _sole_line():
    return SimpleNamespace(
        id=501,
        public_id="eli-pub-1",
        row_version="rv-client",
        expense_id=7,
        sub_cost_code_id=None,
        description="orig",
    )


def _approved_status():
    return SimpleNamespace(
        id=30, name="Approved", is_final=True, is_declined=False, is_active=True
    )


def _declined_status():
    return SimpleNamespace(
        id=100, name="Declined", is_final=False, is_declined=True, is_active=True
    )


def _wire_status_service(RsSvc, *, decision: str = "approved"):
    if decision == "approved":
        RsSvc.return_value.get_approved_status.return_value = _approved_status()
    else:
        RsSvc.return_value.get_declined_statuses.return_value = [_declined_status()]


def _recipient_envelope(*, user_id: int = CALLER_USER_ID):
    recipient = SimpleNamespace(user_id=user_id, email="pm@example.com")
    return {"to": [recipient], "cc": []}


def _patch_get_connection(get_conn):
    mock_conn = MagicMock(name="conn")
    get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
    get_conn.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn


def _wire_review_repo(ReviewRepo, *, current=None, idem_existing=None):
    ReviewRepo.return_value.read_by_idempotency_key.return_value = idem_existing
    ReviewRepo.return_value.read_current_by_expense_id_for_update.return_value = current


def _wire_line_count(EliSvc, count: int = 1):
    EliSvc.return_value.count_by_expense_id_for_update.return_value = count


def _wire_sole_line(EliSvc, lines=None):
    if lines is None:
        lines = [_sole_line()]
    EliSvc.return_value.read_by_expense_id.return_value = lines
    EliSvc.return_value.read_by_expense_id_in_transaction.return_value = lines


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _rbac_override():
    dep = inspect.signature(apply_review_decision_expense_router).parameters[
        "current_user"
    ].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub"}


def test_route_gates_expenses_can_approve():
    endpoint = apply_review_decision_expense_router
    sig = inspect.signature(endpoint)
    param = sig.parameters["current_user"]
    dep = param.default
    assert isinstance(dep, DependsParam)
    inner = dep.dependency
    nonlocals = inspect.getclosurevars(inner).nonlocals
    assert nonlocals.get("module_name") == Modules.EXPENSES
    assert nonlocals.get("permission") == "can_approve"


@_patch_apply_transactional
def test_approved_threads_one_conn_through_line_and_review(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    conn = _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    review = SimpleNamespace(
        public_id="rev-new",
        review_status_id=30,
        user_id=CALLER_USER_ID,
        review_kind="approved",
        expense_id=7,
    )
    ReviewRepo.return_value.create.return_value = review

    result = svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="approved",
        sub_cost_code_public_id=SCC_PID,
        description="Supplies",
        idempotency_key=IDEM_KEY,
        line_row_version="rv-client",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )

    EliSvc.return_value.update_coding_in_transaction.assert_called_once()
    assert (
        EliSvc.return_value.update_coding_in_transaction.call_args.kwargs["conn"] is conn
    )
    create_kw = ReviewRepo.return_value.create.call_args.kwargs
    assert create_kw["conn"] is conn
    assert create_kw["user_id"] == CALLER_USER_ID
    assert create_kw["created_by_user_id"] == CALLER_USER_ID
    assert create_kw["idempotency_key"] == IDEM_KEY
    assert result["replayed"] is False
    assert result["reviewer_user_id"] == CALLER_USER_ID
    assert result["review_status"] == "Approved"
    assert result["expense_public_id"] == EXPENSE_PID
    RsSvc.return_value.read_by_id.assert_not_called()


@_patch_apply_transactional
def test_rejected_skips_line_coding(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    conn = _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    _wire_status_service(RsSvc, decision="rejected")
    _wire_review_repo(ReviewRepo)
    review = SimpleNamespace(
        public_id="rev-declined",
        review_status_id=100,
        user_id=CALLER_USER_ID,
        review_kind="declined",
        expense_id=7,
    )
    ReviewRepo.return_value.create.return_value = review

    result = svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="rejected",
        sub_cost_code_public_id=None,
        description=None,
        idempotency_key=IDEM_KEY,
        line_row_version="rv-client",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )

    EliSvc.return_value.update_coding_in_transaction.assert_not_called()
    assert ReviewRepo.return_value.create.call_args.kwargs["conn"] is conn
    assert result["decision_applied"] == "rejected"


@_patch_apply_transactional
def test_multi_line_refuses_422(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    EliSvc.return_value.read_by_expense_id.return_value = [_sole_line(), _sole_line()]
    _wire_review_repo(ReviewRepo)
    _wire_status_service(RsSvc, decision="rejected")

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            sub_cost_code_public_id=None,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.error_code == ErrorCode.MULTI_LINE_NOT_SUPPORTED
    assert exc.value.status_code == 422
    get_conn.assert_not_called()


@patch("entities.expense.business.service.SubCostCodeService")
@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
def test_zero_lines_pre_transaction_value_error(
    EliSvc, RecipientSvc, ReviewRepo, SccSvc
):
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=9001, is_holding_code=False
    )
    svc = _expense_stub()
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    ReviewRepo.return_value.read_by_idempotency_key.return_value = None
    EliSvc.return_value.read_by_expense_id.return_value = []

    with pytest.raises(ValueError, match="has no line items"):
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )


@_patch_apply_transactional
def test_zero_lines_same_contract_pre_and_in_transaction(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    EliSvc.return_value.read_by_expense_id.return_value = [_sole_line()]
    _wire_line_count(EliSvc, count=0)
    _wire_status_service(RsSvc, decision="approved")
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_review_repo(ReviewRepo)

    with pytest.raises(ValueError, match="has no line items"):
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )


@_patch_apply_transactional
def test_review_state_stale(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(
        ReviewRepo,
        current=SimpleNamespace(public_id="other-review"),
    )

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.error_code == ErrorCode.REVIEW_STATE_STALE


@_patch_apply_transactional
def test_rejected_line_row_version_stale_after_authorization(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    """Reject must re-check line ROWVERSION in-tx (approve already did via recode)."""
    svc = _expense_stub()
    conn = _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    stale_line = _sole_line()
    stale_line.row_version = "rv-moved-after-auth"
    EliSvc.return_value.read_by_expense_id.return_value = [_sole_line()]
    EliSvc.return_value.read_by_expense_id_in_transaction.return_value = [stale_line]
    _wire_line_count(EliSvc)
    _wire_status_service(RsSvc, decision="rejected")
    _wire_review_repo(ReviewRepo)

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            sub_cost_code_public_id=None,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.error_code == ErrorCode.LINE_ROW_VERSION_STALE
    ReviewRepo.return_value.create.assert_not_called()
    assert (
        EliSvc.return_value.read_by_expense_id_in_transaction.call_args_list[
            -1
        ].kwargs.get("conn")
        is conn
    )


@_patch_apply_transactional
def test_concurrent_duplicate_replays_after_parent_lock(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    replay = SimpleNamespace(
        public_id="rev-replay",
        review_status_id=30,
        user_id=CALLER_USER_ID,
        review_kind="approved",
        expense_id=7,
    )
    # The first request has committed: under the parent lock its Review is CURRENT, so
    # the expected-review precondition (expected None) would say stale — the replay
    # must be checked BEFORE the precondition.
    ReviewRepo.return_value.read_current_by_expense_id_for_update.return_value = replay
    ReviewRepo.return_value.read_by_idempotency_key.side_effect = [None, replay]

    result = svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="approved",
        sub_cost_code_public_id=SCC_PID,
        description=None,
        idempotency_key=IDEM_KEY,
        line_row_version="rv-client",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )
    assert result["replayed"] is True
    assert result["review_public_id"] == "rev-replay"
    ReviewRepo.return_value.create.assert_not_called()


@_patch_apply_transactional
def test_replay_after_line_split_skips_multi_line_refusal(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=9001, is_holding_code=False
    )
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    EliSvc.return_value.read_by_expense_id.return_value = [
        _sole_line(),
        _sole_line(),
    ]
    _wire_status_service(RsSvc, decision="approved")
    prior = SimpleNamespace(
        public_id="rev-prior",
        review_status_id=30,
        user_id=CALLER_USER_ID,
        review_kind="approved",
        expense_id=7,
    )
    _wire_review_repo(ReviewRepo, idem_existing=prior)

    result = svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="approved",
        sub_cost_code_public_id=SCC_PID,
        description=None,
        idempotency_key=IDEM_KEY,
        line_row_version="rv-client",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )
    assert result["replayed"] is True
    assert result["review_public_id"] == "rev-prior"
    get_conn.assert_not_called()


@_patch_apply_transactional
def test_idempotency_key_conflict_on_different_expense(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(
        ReviewRepo,
        idem_existing=SimpleNamespace(
            public_id="rev-other",
            expense_id=999,
            review_status_id=30,
            user_id=CALLER_USER_ID,
            review_kind="approved",
        ),
    )

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.error_code == ErrorCode.IDEMPOTENCY_KEY_CONFLICT


@_patch_apply_transactional
def test_line_row_version_stale(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    EliSvc.return_value.update_coding_in_transaction.side_effect = (
        DatabaseConcurrencyError("conflict")
    )

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.error_code == ErrorCode.LINE_ROW_VERSION_STALE


@_patch_apply_transactional
def test_status_locked_422_not_409(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    mock_cm = get_conn.return_value
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    ReviewRepo.return_value.create.side_effect = StatusLockedError(
        "This document is completed and can no longer be edited: Expense"
    )

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.status_code == 422
    assert exc.value.error_code == ErrorCode.STATUS_LOCKED
    EliSvc.return_value.update_coding_in_transaction.assert_called_once()
    mock_cm.__exit__.assert_called_once()
    assert mock_cm.__exit__.call_args[0][1] is not None


@_patch_apply_transactional
def test_review_create_failure_rolls_back_line_recode(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    mock_cm = get_conn.return_value
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    ReviewRepo.return_value.create.side_effect = RuntimeError("review insert failed")

    with pytest.raises(RuntimeError, match="review insert failed"):
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    EliSvc.return_value.update_coding_in_transaction.assert_called_once()
    mock_cm.__exit__.assert_called_once()
    assert mock_cm.__exit__.call_args[0][1] is not None


@_patch_apply_transactional
def test_idempotency_retry_after_success_returns_replayed_payload(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    review = SimpleNamespace(
        public_id="rev-first",
        review_status_id=30,
        user_id=CALLER_USER_ID,
        review_kind="approved",
        expense_id=7,
    )
    ReviewRepo.return_value.create.return_value = review

    first = svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="approved",
        sub_cost_code_public_id=SCC_PID,
        description=None,
        idempotency_key=IDEM_KEY,
        line_row_version="rv-client",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )
    assert first["replayed"] is False
    assert first["review_public_id"] == "rev-first"
    assert ReviewRepo.return_value.create.call_count == 1

    ReviewRepo.return_value.read_by_idempotency_key.return_value = review
    second = svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="approved",
        sub_cost_code_public_id=SCC_PID,
        description=None,
        idempotency_key=IDEM_KEY,
        line_row_version="rv-client",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )
    assert second["replayed"] is True
    assert second["review_public_id"] == "rev-first"
    assert ReviewRepo.return_value.create.call_count == 1


@_patch_apply_transactional
def test_concurrent_duplicate_insert_replays(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    ReviewRepo.return_value.create.side_effect = DatabaseConstraintError(
        UNIQUE_VIOLATION, original="dup"
    )
    replay = SimpleNamespace(
        public_id="rev-replay",
        review_status_id=30,
        user_id=CALLER_USER_ID,
        review_kind="approved",
        expense_id=7,
    )
    ReviewRepo.return_value.read_by_idempotency_key.side_effect = [None, replay]

    result = svc.apply_transactional_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="approved",
        sub_cost_code_public_id=SCC_PID,
        description=None,
        idempotency_key=IDEM_KEY,
        line_row_version="rv-client",
        expected_review_public_id=None,
        caller_user_id=CALLER_USER_ID,
    )
    assert result["replayed"] is True
    assert result["review_public_id"] == "rev-replay"


@_patch_apply_transactional
def test_concurrent_duplicate_insert_wrong_expense_raises_conflict(
    RecipientSvc,
    get_conn,
    ReviewRepo,
    RsSvc,
    EliSvc,
    SccSvc,
):
    svc = _expense_stub()
    _patch_get_connection(get_conn)
    RecipientSvc.return_value.resolve_for_expense.return_value = _recipient_envelope()
    _wire_sole_line(EliSvc)
    _wire_line_count(EliSvc)
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_status_service(RsSvc, decision="approved")
    _wire_review_repo(ReviewRepo)
    ReviewRepo.return_value.create.side_effect = DatabaseConstraintError(
        UNIQUE_VIOLATION, original="dup"
    )
    foreign_review = SimpleNamespace(
        public_id="rev-wrong",
        review_status_id=30,
        user_id=CALLER_USER_ID,
        review_kind="approved",
        expense_id=999,
    )
    ReviewRepo.return_value.read_by_idempotency_key.side_effect = [
        None,
        None,
        foreign_review,
    ]

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            sub_cost_code_public_id=SCC_PID,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.error_code == ErrorCode.IDEMPOTENCY_KEY_CONFLICT
    ReviewRepo.return_value.create.assert_called_once()


@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_not_a_reviewer_403(RecipientSvc):
    svc = _expense_stub()
    RecipientSvc.return_value.resolve_for_expense.return_value = {
        "to": [SimpleNamespace(user_id=OTHER_USER_ID, email="other@example.com")],
        "cc": [],
    }

    with pytest.raises(ReviewDecisionApplyError) as exc:
        svc.apply_transactional_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            sub_cost_code_public_id=None,
            description=None,
            idempotency_key=IDEM_KEY,
            line_row_version="rv-client",
            expected_review_public_id=None,
            caller_user_id=CALLER_USER_ID,
        )
    assert exc.value.error_code == ErrorCode.NOT_A_REVIEWER
    assert exc.value.status_code == 403


def test_request_schema_has_no_reviewer_identity_field():
    fields = ExpenseReviewDecisionApplyRequest.model_fields
    for forbidden in (
        "reviewer_email",
        "reviewer_user_id",
        "user_id",
        "created_by_user_id",
    ):
        assert forbidden not in fields


def test_new_command_has_no_assert_may_act_as():
    from entities.review.business.reviewer_decision import apply_transactional  # U-755

    src = inspect.getsource(apply_transactional)
    assert "assert_may_act_as" not in src
    # The entity wrapper must not call the guard before delegating either.
    wrapper = inspect.getsource(ExpenseService.apply_transactional_reviewer_decision)
    assert "assert_may_act_as" not in wrapper


def test_apply_route_is_registered_on_review_router():
    paths = {
        getattr(r, "path", None)
        for r in review_router.routes
        if "POST" in (getattr(r, "methods", None) or set())
    }
    assert "/api/v1/apply/review-decision/expense/{public_id}" in paths


@pytest.mark.parametrize(
    "side_effect,expected_status,expected_error_code",
    [
        (
            review_decision_apply_error(
                ErrorCode.NOT_A_REVIEWER,
                status_code=403,
                parent="expense",
            ),
            403,
            ErrorCode.NOT_A_REVIEWER,
        ),
        (
            review_decision_apply_error(ErrorCode.LINE_ROW_VERSION_STALE, parent="expense"),
            409,
            ErrorCode.LINE_ROW_VERSION_STALE,
        ),
        (
            review_decision_apply_error(ErrorCode.REVIEW_STATE_STALE, parent="expense"),
            409,
            ErrorCode.REVIEW_STATE_STALE,
        ),
        (
            review_decision_apply_error(
                ErrorCode.IDEMPOTENCY_KEY_CONFLICT, parent="expense"
            ),
            409,
            ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
        ),
        (
            review_decision_apply_error(
                ErrorCode.MULTI_LINE_NOT_SUPPORTED,
                status_code=422,
                parent="expense",
            ),
            422,
            ErrorCode.MULTI_LINE_NOT_SUPPORTED,
        ),
        (
            ReviewDecisionApplyError(
                status_code=422,
                detail="This document is completed and can no longer be edited: Expense",
                error_code=ErrorCode.STATUS_LOCKED,
            ),
            422,
            ErrorCode.STATUS_LOCKED,
        ),
    ],
)
@patch("entities.review.api.router.resolve_user_id", return_value=CALLER_USER_ID)
@patch("entities.review.api.router.ExpenseService")
def test_endpoint_error_codes(
    ExpenseSvc,
    _resolve,
    side_effect,
    expected_status,
    expected_error_code,
    client,
    clear_dependency_overrides,
):
    _rbac_override()
    ExpenseSvc.return_value.apply_transactional_reviewer_decision.side_effect = (
        side_effect
    )
    response = client.post(APPLY_PATH, json=_request_json())
    assert response.status_code == expected_status
    body = response.json()
    assert body["error_code"] == expected_error_code
    assert body["detail"] != expected_error_code


@patch("entities.review.api.router.resolve_user_id", return_value=CALLER_USER_ID)
@patch("entities.review.api.router.ExpenseService")
def test_endpoint_success_replayed(
    ExpenseSvc,
    _resolve,
    client,
    clear_dependency_overrides,
):
    _rbac_override()
    ExpenseSvc.return_value.apply_transactional_reviewer_decision.return_value = {
        "decision_applied": "approved",
        "review_status": "Approved",
        "reviewer_user_id": CALLER_USER_ID,
        "is_draft": True,
        "expense_public_id": EXPENSE_PID,
        "review_public_id": "rev-first",
        "replayed": True,
    }
    response = client.post(APPLY_PATH, json=_request_json())
    assert response.status_code == 200
    assert response.json()["data"]["replayed"] is True


@pytest.mark.parametrize(
    "parent,code,text",
    [
        ("bill", "not_a_reviewer", "You are not a reviewer for this bill."),
        ("expense", "not_a_reviewer", "You are not a reviewer for this expense."),
        ("bill", "line_row_version_stale", "The bill line was updated by someone else; refetch and re-present."),
        ("expense", "line_row_version_stale", "The expense line was updated by someone else; refetch and re-present."),
        ("bill", "review_state_stale", "The review state has changed; drop this queued action."),
        ("expense", "review_state_stale", "The review state has changed; drop this queued action."),
        ("bill", "idempotency_key_conflict", "This idempotency key was already used for a different bill."),
        ("expense", "idempotency_key_conflict", "This idempotency key was already used for a different expense."),
        ("bill", "multi_line_not_supported", "Multi-line bills are not supported for reviewer decisions in the app."),
        ("expense", "multi_line_not_supported", "Multi-line expenses are not supported for reviewer decisions in the app."),
    ],
)
def test_decision_error_wording_is_pinned_per_parent(parent, code, text):
    """The shared wording template must render exactly the pre-template strings."""
    from entities.review.business.decision_errors import review_decision_apply_error

    assert review_decision_apply_error(code, parent=parent).detail == text


# --- U-551b r2 (F2 P2): the in-transaction line read must NOT be deadlock-retried ---


def test_in_transaction_line_read_is_not_retried_on_deadlock():
    """A 40001 victim rolls back the WHOLE transaction (dropping the caller's
    UPDLOCKs); a decorator retry on the same conn would run outside those
    locks. The in-transaction read must propagate; the standalone read (own
    connection) must still retry — pinned from both sides."""
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from entities.expense_line_item.persistence import repo as repo_mod

    executes = []
    # The repo maps raw pyodbc errors BEFORE the retry decorator sees them, and
    # a mapped 40001 is not is_transient_error() — so a raw pyodbc.Error would
    # make this pin vacuous (no retry on either side). Raise an already-mapped
    # DatabaseError that IS transient (SQLSTATE 40001 in args[0]) instead;
    # map_database_error passes DatabaseError through unchanged.
    deadlock = DatabaseConcurrencyError("40001")
    assert is_transient_error(deadlock)

    def deadlocking_call_procedure(*, cursor, name, params):
        executes.append(name)
        raise deadlock

    sleeps = []
    standalone_conn = MagicMock()
    standalone_conn.__enter__.return_value = standalone_conn
    standalone_conn.__exit__.return_value = False

    with patch.object(repo_mod, "call_procedure", deadlocking_call_procedure), patch(
        "shared.database.time.sleep", side_effect=sleeps.append
    ), patch.object(repo_mod, "get_connection", return_value=standalone_conn):
        svc = ExpenseLineItemService()
        txn_conn = MagicMock()

        # In-transaction: ONE execute, error propagates, no sleep, no 2nd attempt.
        with pytest.raises(DatabaseConcurrencyError) as excinfo:
            svc.read_by_expense_id_in_transaction(7, conn=txn_conn)
        assert excinfo.value is deadlock
        assert len(executes) == 1
        assert sleeps == []

        # Standalone (no conn): still retried — max_retries=3 => 4 attempts.
        executes.clear()
        with pytest.raises(DatabaseConcurrencyError):
            svc.repo.read_by_expense_id(7)
        assert len(executes) == 4
        assert len(sleeps) == 3
