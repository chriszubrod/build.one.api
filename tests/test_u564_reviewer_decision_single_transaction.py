"""U-564 — emailed reviewer decision: line coding + Review insert share one transaction."""

from __future__ import annotations

import re
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business.recipient_model import ResolvedRecipient
from shared.authz.context import clear_authz_context, set_authz_context, system_authz
from shared.authz.provenance import ReviewerProvenanceRefused

REPO_ROOT = Path(__file__).resolve().parents[1]

BILL_PID = "bill-pub-u564"
EXPENSE_PID = "exp-pub-u564"
REVIEWER_USER_ID = 20
AUTH_CALLER_ID = 33
SCC_PID = "scc-pub-u564"
REPLY_MSG_PUB = "em-reply-u564"


class _ConnTracker:
    """Records context-manager enter/exit for get_connection patches."""

    def __init__(self):
        self.conn = MagicMock(name="tracked_conn")
        self.enter_count = 0
        self.exit_exc_type = None

    def __enter__(self):
        self.enter_count += 1
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exit_exc_type = exc_type
        return False


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _patch_get_connection_factory(get_conn):
    tracker = _ConnTracker()
    get_conn.return_value = tracker
    return tracker


def _pm_recipient() -> ResolvedRecipient:
    return ResolvedRecipient(
        user_id=REVIEWER_USER_ID,
        firstname="Pat",
        lastname="M",
        email="pm@example.com",
        role_name="Project Manager",
        project_id=1,
    )


def _approved_statuses():
    declined = SimpleNamespace(id=100, name="Declined", is_final=False, is_declined=True)
    approved = SimpleNamespace(id=30, name="Approved", is_final=True, is_declined=False)
    return [declined, approved]


def _u563_provenance_ok(reviewer_email: str = "pm@example.com"):
    return patch(
        "entities.email_message.business.service.EmailMessageService",
        return_value=MagicMock(
            read_by_public_id=MagicMock(
                return_value=SimpleNamespace(
                    id=888,
                    folder="inbox",
                    from_address=reviewer_email,
                    conversation_id="review-thread",
                )
            )
        ),
    )


@pytest.fixture(autouse=True)
def _u563b_review_thread_anchor():
    with patch(
        "shared.authz.provenance.expected_review_thread_conversation_ids",
        side_effect=lambda **_kw: (
            {_kw["reply_conversation_id"]}
            if _kw.get("reply_conversation_id")
            else set()
        ),
    ):
        yield


def _bill_service_stub(*, bill_id: int = 7):
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=bill_id,
            public_id=BILL_PID,
            is_draft=True,
        )
    )
    return svc


def _expense_service_stub(*, expense_id: int = 101):
    from entities.expense.business.service import ExpenseService

    svc = ExpenseService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=expense_id,
            public_id=EXPENSE_PID,
            is_draft=True,
        )
    )
    return svc


def _bill_line():
    return SimpleNamespace(
        id=501,
        public_id="bli-pub-1",
        row_version="rv-base64",
        bill_id=7,
        sub_cost_code_id=None,
        description="orig",
    )


def _expense_line():
    return SimpleNamespace(
        id=601,
        public_id="eli-pub-1",
        row_version="rv1",
        expense_id=101,
        sub_cost_code_id=None,
        description="orig",
    )


def _wire_bill_recipients(RecipSvc):
    RecipSvc.return_value.resolve_for_bill.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }


def _wire_expense_recipients(RecipSvc):
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }


def _wire_review_status(RsSvc, *, empty_approved: bool = False, empty_declined: bool = False):
    if empty_approved:
        RsSvc.return_value.read_all.return_value = [
            SimpleNamespace(id=100, is_final=False, is_declined=True),
        ]
    elif empty_declined:
        RsSvc.return_value.read_all.return_value = [
            SimpleNamespace(id=30, is_final=True, is_declined=False),
        ]
    else:
        RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")


BILL_PATCH_TARGETS = (
    "entities.bill.business.service.get_connection",
    "entities.review.persistence.repo.ReviewRepository",
    "entities.review_status.business.service.ReviewStatusService",
    "entities.bill_line_item.business.service.BillLineItemService",
    "entities.bill.business.service.SubCostCodeService",
    "entities.review.business.recipient_service.ReviewRecipientService",
)

EXPENSE_PATCH_TARGETS = (
    "entities.expense.business.service.get_connection",
    "entities.review.persistence.repo.ReviewRepository",
    "entities.review_status.business.service.ReviewStatusService",
    "entities.expense_line_item.business.service.ExpenseLineItemService",
    "entities.expense.business.service.SubCostCodeService",
    "entities.review.business.recipient_service.ReviewRecipientService",
)


def _patch_stack(targets):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        for target in reversed(targets):
            wrapper = patch(target)(wrapper)
        return wrapper

    return decorator


@_patch_stack(BILL_PATCH_TARGETS)
def test_bill_rejection_skips_line_coding_create_uses_conn(
    RecipSvc,
    SccSvc,
    BliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _bill_service_stub()
    _wire_bill_recipients(RecipSvc)
    _wire_review_status(RsSvc)
    ReviewRepo.return_value.read_current_by_bill_id_for_update.return_value = None
    ReviewRepo.return_value.create.return_value = SimpleNamespace(review_status_id=100)
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")

    with system_authz(), _u563_provenance_ok():
        svc.apply_reviewer_decision(
            bill_public_id=BILL_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    BliSvc.return_value.update_coding_in_transaction.assert_not_called()
    ReviewRepo.return_value.create.assert_called_once()
    assert ReviewRepo.return_value.create.call_args.kwargs["conn"] is tracker.conn


@_patch_stack(EXPENSE_PATCH_TARGETS)
def test_expense_under_lock_multi_line_refuses_without_create(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _expense_service_stub()
    _wire_expense_recipients(RecipSvc)
    EliSvc.return_value.read_by_expense_id.return_value = [_expense_line()]
    EliSvc.return_value.count_by_expense_id_for_update.return_value = 2
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_review_status(RsSvc)
    ReviewRepo.return_value.read_current_by_expense_id_for_update.return_value = None

    with system_authz(), _u563_provenance_ok():
        with pytest.raises(ValueError, match="multi-line"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    ReviewRepo.return_value.create.assert_not_called()
    EliSvc.return_value.update_coding_in_transaction.assert_not_called()
    assert tracker.enter_count == 1


@_patch_stack(BILL_PATCH_TARGETS)
def test_bill_unknown_scc_raises_before_get_connection(
    RecipSvc,
    SccSvc,
    BliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _bill_service_stub()
    _wire_bill_recipients(RecipSvc)
    BliSvc.return_value.read_by_bill_id.return_value = [_bill_line()]
    SccSvc.return_value.read_by_public_id.return_value = None

    with system_authz(), _u563_provenance_ok():
        with pytest.raises(ValueError, match="SubCostCode"):
            svc.apply_reviewer_decision(
                bill_public_id=BILL_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert tracker.enter_count == 0
    ReviewRepo.return_value.create.assert_not_called()


@_patch_stack(EXPENSE_PATCH_TARGETS)
def test_expense_unknown_scc_raises_before_get_connection(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _expense_service_stub()
    _wire_expense_recipients(RecipSvc)
    EliSvc.return_value.read_by_expense_id.return_value = [_expense_line()]
    SccSvc.return_value.read_by_public_id.return_value = None

    with system_authz(), _u563_provenance_ok():
        with pytest.raises(ValueError, match="SubCostCode"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert tracker.enter_count == 0
    ReviewRepo.return_value.create.assert_not_called()


@_patch_stack(BILL_PATCH_TARGETS)
def test_bill_no_approved_status_raises_before_get_connection(
    RecipSvc,
    SccSvc,
    BliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _bill_service_stub()
    _wire_bill_recipients(RecipSvc)
    BliSvc.return_value.read_by_bill_id.return_value = [_bill_line()]
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_review_status(RsSvc, empty_approved=True)

    with system_authz(), _u563_provenance_ok():
        with pytest.raises(ValueError, match="terminal non-declined"):
            svc.apply_reviewer_decision(
                bill_public_id=BILL_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert tracker.enter_count == 0
    ReviewRepo.return_value.create.assert_not_called()


@_patch_stack(EXPENSE_PATCH_TARGETS)
def test_expense_no_approved_status_raises_before_get_connection(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _expense_service_stub()
    _wire_expense_recipients(RecipSvc)
    EliSvc.return_value.read_by_expense_id.return_value = [_expense_line()]
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_review_status(RsSvc, empty_approved=True)

    with system_authz(), _u563_provenance_ok():
        with pytest.raises(ValueError, match="terminal non-declined"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert tracker.enter_count == 0
    ReviewRepo.return_value.create.assert_not_called()


@_patch_stack(EXPENSE_PATCH_TARGETS)
def test_expense_multi_line_pre_lock_raises_before_get_connection(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _expense_service_stub()
    _wire_expense_recipients(RecipSvc)
    EliSvc.return_value.read_by_expense_id.return_value = [_expense_line(), _expense_line()]

    with system_authz(), _u563_provenance_ok():
        with pytest.raises(ValueError, match="multi-line"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert tracker.enter_count == 0
    ReviewRepo.return_value.create.assert_not_called()


@_patch_stack(BILL_PATCH_TARGETS)
def test_provenance_refusal_before_get_connection_bill(
    RecipSvc,
    SccSvc,
    BliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _patch_get_connection_factory(get_conn)
    svc = _bill_service_stub()
    _wire_bill_recipients(RecipSvc)
    BliSvc.return_value.read_by_bill_id.return_value = [_bill_line()]

    with system_authz():
        with patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as Em:
            Em.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=888,
                folder="sent items",
                from_address="pm@example.com",
                conversation_id="x",
            )
            with pytest.raises(ReviewerProvenanceRefused):
                svc.apply_reviewer_decision(
                    bill_public_id=BILL_PID,
                    decision="approved",
                    reviewer_email="pm@example.com",
                    sub_cost_code_public_id=SCC_PID,
                    reviewer_email_message_public_id=REPLY_MSG_PUB,
                )

    assert tracker.enter_count == 0
    ReviewRepo.return_value.create.assert_not_called()


@_patch_stack(BILL_PATCH_TARGETS)
def test_bill_created_by_is_authenticated_caller_user_id_is_reviewer(
    RecipSvc,
    SccSvc,
    BliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    _patch_get_connection_factory(get_conn)
    svc = _bill_service_stub()
    _wire_bill_recipients(RecipSvc)
    BliSvc.return_value.read_by_bill_id.return_value = [_bill_line()]
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_review_status(RsSvc)
    ReviewRepo.return_value.read_current_by_bill_id_for_update.return_value = None
    ReviewRepo.return_value.create.return_value = SimpleNamespace(review_status_id=30)

    set_authz_context(user_id=AUTH_CALLER_ID, company_id=1, is_system_admin=True)
    with _u563_provenance_ok():
        svc.apply_reviewer_decision(
            bill_public_id=BILL_PID,
            decision="approved",
            reviewer_email="pm@example.com",
            sub_cost_code_public_id=SCC_PID,
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    create_kw = ReviewRepo.return_value.create.call_args.kwargs
    assert create_kw["user_id"] == REVIEWER_USER_ID
    assert create_kw["created_by_user_id"] == AUTH_CALLER_ID


@_patch_stack(EXPENSE_PATCH_TARGETS)
def test_expense_created_by_is_authenticated_caller_user_id_is_reviewer(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    _patch_get_connection_factory(get_conn)
    svc = _expense_service_stub()
    _wire_expense_recipients(RecipSvc)
    EliSvc.return_value.read_by_expense_id.return_value = [_expense_line()]
    EliSvc.return_value.count_by_expense_id_for_update.return_value = 1
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    _wire_review_status(RsSvc)
    ReviewRepo.return_value.read_current_by_expense_id_for_update.return_value = None
    ReviewRepo.return_value.create.return_value = SimpleNamespace(review_status_id=30)

    set_authz_context(user_id=AUTH_CALLER_ID, company_id=1, is_system_admin=True)
    with _u563_provenance_ok():
        svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            reviewer_email="pm@example.com",
            sub_cost_code_public_id=SCC_PID,
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    create_kw = ReviewRepo.return_value.create.call_args.kwargs
    assert create_kw["user_id"] == REVIEWER_USER_ID
    assert create_kw["created_by_user_id"] == AUTH_CALLER_ID


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.sub_cost_code.business.service.SubCostCodeService")
@patch("entities.contract_labor.business.service.ProjectService")
@patch("entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository")
@patch("shared.database.get_connection")
def test_contract_labor_created_by_is_authenticated_caller(
    get_conn,
    LiRepo,
    ProjSvc,
    SccSvc,
    RsSvc,
    ReviewRepo,
):
    from entities.contract_labor.business.service import ContractLaborService

    tracker = _ConnTracker()
    get_conn.return_value = tracker
    mock_cursor = MagicMock()
    tracker.conn.cursor.return_value = mock_cursor
    recipient_row = SimpleNamespace(
        ProjectId=9, Email="pm@example.com", UserId=REVIEWER_USER_ID
    )
    mock_cursor.fetchall.return_value = [recipient_row]

    svc = ContractLaborService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=50, public_id="cl-pub", status="submitted")
    )
    ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=9, public_id="proj-pub"
    )

    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    LiRepo.return_value.read_by_contract_labor_id.return_value = [
        SimpleNamespace(
            id=1,
            project_id=9,
            row_version_bytes=b"rv",
            line_date="2026-01-01",
            description="d",
            hours=1,
            rate=1,
            markup=0,
            price=1,
            is_billable=True,
            is_overhead=False,
            bill_line_item_id=None,
        )
    ]
    LiRepo.return_value.update_by_id.return_value = SimpleNamespace(id=1)
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")
    ReviewRepo.return_value.create.return_value = SimpleNamespace(
        id=900,
        review_status_id=100,
        status_is_final=False,
        status_is_declined=True,
    )
    RsSvc.return_value.read_all.return_value = [
        SimpleNamespace(id=100, is_final=False, is_declined=True),
        SimpleNamespace(id=30, is_final=True, is_declined=False),
    ]

    with patch(
        "entities.email_message.business.service.EmailMessageService"
    ) as Em:
        Em.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=777,
            folder="inbox",
            from_address="pm@example.com",
            conversation_id="cl-thread",
        )
        svc._expected_cl_reviewer_reply_conversation_id = MagicMock(
            return_value="cl-thread"
        )
        set_authz_context(user_id=AUTH_CALLER_ID, company_id=1, is_system_admin=True)
        svc._apply_decision_to_single_cl(
            contract_labor_public_id="cl-pub",
            project_public_id="proj-pub",
            decision="rejected",
            reviewer_email="pm@example.com",
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    create_kw = ReviewRepo.return_value.create.call_args.kwargs
    assert create_kw["user_id"] == REVIEWER_USER_ID
    assert create_kw["created_by_user_id"] == AUTH_CALLER_ID


@pytest.mark.parametrize(
    "sql_rel,sproc_name,locked",
    [
        ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "CountExpenseLineItemsByExpenseIdForUpdate",
         "FROM dbo.[ExpenseLineItem] WITH (UPDLOCK, HOLDLOCK)"),
        # The PARENT row is what serialises two decisions on one expense.
        ("entities/review/sql/dbo.review.sql", "ReadCurrentReviewByExpenseIdForUpdate",
         "FROM dbo.[Expense] WITH (UPDLOCK, HOLDLOCK)"),
    ],
)
def test_new_sprocs_take_the_lock(sql_rel, sproc_name, locked):
    # Defined-once, NOCOUNT and the Sprocs header are enforced repo-wide by
    # test_sproc_single_source / test_sproc_nocount_shape_guard / test_entity_docstrings;
    # what is specific to U-564 is that both reads take UPDLOCK + HOLDLOCK.
    from tests.sproc_text import sproc_body

    body = sproc_body(REPO_ROOT / sql_rel, sproc_name)
    assert locked in body, f"{sproc_name} must take {locked}"


# --- Real service + repository chain: only the database calls are faked -------------
#
# The tests above mock the line service and ReviewRepository, so a line write that
# quietly opened its OWN connection (and committed) would pass them. These run the
# real ExpenseLineItemService / BillLineItemService -> repository -> call_procedure
# chain and the real ReviewRepository.create, fake only get_connection and
# call_procedure, and record which connection served each write.


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.last = None

    def fetchone(self):
        return self.conn.rows.get(self.last)

    def nextset(self):
        return False


class _FakeConn:
    def __init__(self, name, rows):
        self.name = name
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self)


class _FakeCM:
    def __init__(self, conn):
        self.conn = conn
        self.exited_with = "not exited"

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.exited_with = exc_type
        return False


def _real_chain(monkeypatch, *, service_module, line_repo_module, fail_on="CreateReview"):
    rows = {
        "CountExpenseLineItemsByExpenseIdForUpdate": SimpleNamespace(LineCount=1),
        "UpdateExpenseLineItemById": SimpleNamespace(),
        "UpdateBillLineItemById": SimpleNamespace(),
    }
    outer = _FakeCM(_FakeConn("decision-transaction", rows))
    stray = []  # any connection a repository opens on its own

    def stray_connection():
        cm = _FakeCM(_FakeConn("SEPARATE", rows))
        stray.append(cm)
        return cm

    calls = []

    def fake_call_procedure(*, cursor, name, params):
        cursor.last = name
        calls.append((name, cursor.conn.name))
        if name == fail_on:
            raise RuntimeError(f"{name} failed")

    monkeypatch.setattr(f"{service_module}.get_connection", lambda *a, **k: outer)
    for mod in (line_repo_module, "entities.review.persistence.repo"):
        monkeypatch.setattr(f"{mod}.get_connection", lambda *a, **k: stray_connection())
        monkeypatch.setattr(f"{mod}.call_procedure", fake_call_procedure)
    return outer, stray, calls


def _writes(calls):
    return [c for c in calls if c[0].startswith(("Update", "Create"))]


def test_expense_real_chain_line_write_and_review_share_the_transaction_and_roll_back_together(monkeypatch):
    from entities.expense_line_item.business.model import ExpenseLineItem
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository

    outer, stray, calls = _real_chain(
        monkeypatch,
        service_module="entities.expense.business.service",
        line_repo_module="entities.expense_line_item.persistence.repo",
    )
    line = ExpenseLineItem(
        id=601, public_id="eli-pub-1", row_version="AAAAAAAAB9E=", created_datetime=None,
        modified_datetime=None, expense_id=101, sub_cost_code_id=None, project_id=None,
        description="orig", quantity=None, rate=None, amount=None, is_billable=None,
        is_billed=None, markup=None, price=None, is_draft=True,
    )
    monkeypatch.setattr(ExpenseLineItemService, "read_by_expense_id", lambda self, **kw: [line])
    monkeypatch.setattr(ExpenseLineItemRepository, "_from_db", lambda self, row: line)
    svc = _expense_service_stub()

    with patch("entities.review.business.recipient_service.ReviewRecipientService") as RecipSvc, \
         patch("entities.review_status.business.service.ReviewStatusService") as RsSvc, \
         patch("entities.expense.business.service.SubCostCodeService") as SccSvc, \
         system_authz(), _u563_provenance_ok():
        _wire_expense_recipients(RecipSvc)
        _wire_review_status(RsSvc)
        SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        with pytest.raises(Exception, match="CreateReview failed"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    # The parent lock is taken on the decision transaction BEFORE any write.
    assert calls[0] == ("ReadCurrentReviewByExpenseIdForUpdate", "decision-transaction"), calls
    assert _writes(calls) == [
        ("UpdateExpenseLineItemById", "decision-transaction"),
        ("CreateReview", "decision-transaction"),
    ], calls
    assert stray == [], "a repository opened its own connection inside the decision"
    assert outer.exited_with is not None and outer.exited_with != "not exited", (
        "the decision transaction must exit with the failure, so get_connection rolls back the line write"
    )


def test_bill_real_chain_line_write_and_review_share_the_transaction_and_roll_back_together(monkeypatch):
    from entities.bill_line_item.business.service import BillLineItemService
    from entities.bill_line_item.persistence.repo import BillLineItemRepository

    outer, stray, calls = _real_chain(
        monkeypatch,
        service_module="entities.bill.business.service",
        line_repo_module="entities.bill_line_item.persistence.repo",
    )
    line = SimpleNamespace(
        id=501, public_id="bli-pub-1", row_version="AAAAAAAAB9E=", row_version_bytes=b"\x00" * 8,
        bill_id=7, sub_cost_code_id=None, project_id=None, description="orig",
        quantity=None, rate=None, amount=None, is_billable=None, is_billed=None,
        markup=None, price=None, is_draft=True,
    )
    monkeypatch.setattr(BillLineItemService, "read_by_bill_id", lambda self, **kw: [line])
    monkeypatch.setattr(BillLineItemRepository, "_from_db", lambda self, row: line)
    svc = _bill_service_stub()

    with patch("entities.review.business.recipient_service.ReviewRecipientService") as RecipSvc, \
         patch("entities.review_status.business.service.ReviewStatusService") as RsSvc, \
         patch("entities.bill.business.service.SubCostCodeService") as SccSvc, \
         system_authz(), _u563_provenance_ok():
        _wire_bill_recipients(RecipSvc)
        _wire_review_status(RsSvc)
        SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        with pytest.raises(Exception, match="CreateReview failed"):
            svc.apply_reviewer_decision(
                bill_public_id=BILL_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    # The parent lock is taken on the decision transaction BEFORE any write.
    assert calls[0] == ("ReadCurrentReviewByBillIdForUpdate", "decision-transaction"), calls
    assert _writes(calls) == [
        ("UpdateBillLineItemById", "decision-transaction"),
        ("CreateReview", "decision-transaction"),
    ], calls
    assert stray == [], "a repository opened its own connection inside the decision"
    assert outer.exited_with not in (None, "not exited")
