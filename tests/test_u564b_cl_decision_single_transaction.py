"""U-564b — ContractLabor reviewer decision: one transaction per crew member."""

from __future__ import annotations

from functools import wraps
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository
from entities.contract_labor.persistence.repo import ContractLaborRepository
from shared.authz.context import clear_authz_context, set_authz_context, system_authz


CL_PID = "cl-pub-u564b"
PROJ_PID = "proj-u564b"
REVIEWER_USER_ID = 20
AUTH_CALLER_ID = 33
SCC_PID = "scc-u564b"
REPLY_MSG_PUB = "em-reply-u564b"
PROJECT_ID = 9
CL_ID = 50


class _ConnTracker:
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
                    conversation_id="cl-review-thread",
                )
            )
        ),
    )


def _cl_line(*, li_id: int, project_id: int = PROJECT_ID, sub_cost_code_id=None):
    return SimpleNamespace(
        id=li_id,
        project_id=project_id,
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
        sub_cost_code_id=sub_cost_code_id,
    )


def _cl_service_stub(*, cl_id: int = CL_ID):
    svc = ContractLaborService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=cl_id,
            public_id=CL_PID,
            status="submitted",
        )
    )
    svc._expected_cl_reviewer_reply_conversation_id = MagicMock(
        return_value="cl-review-thread"
    )
    svc.repo.update_aggregates = MagicMock(
        side_effect=lambda **kw: SimpleNamespace(id=cl_id, status="submitted")
    )
    svc.mark_as_ready_via_review_approval = MagicMock(
        return_value=SimpleNamespace(id=cl_id, status="ready")
    )
    return svc


def _wire_recipients(mock_cursor):
    mock_cursor.fetchall.return_value = [
        SimpleNamespace(ProjectId=PROJECT_ID, Email="pm@example.com", UserId=REVIEWER_USER_ID)
    ]


CL_PATCH_TARGETS = (
    "entities.contract_labor.business.service.get_connection",
    "entities.review.persistence.repo.ReviewRepository",
    "entities.review_status.business.service.ReviewStatusService",
    "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository",
    "entities.contract_labor.business.service.ProjectService",
    "entities.sub_cost_code.business.service.SubCostCodeService",
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


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.last = None

    def fetchone(self):
        return self.conn.rows.get(self.last)

    def fetchall(self):
        val = self.conn.rows.get(self.last)
        if isinstance(val, list):
            return val
        return []

    def nextset(self):
        return False


class _FakeConn:
    def __init__(self, name, rows):
        self.name = name
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self)


class _FakeCM:
    def __init__(self, conn, *, marks_decision_active=False):
        self.conn = conn
        self.exited_with = "not exited"
        self.marks_decision_active = marks_decision_active

    def __enter__(self):
        if self.marks_decision_active:
            self.conn.decision_active["active"] = True
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if self.marks_decision_active:
            self.conn.decision_active["active"] = False
        self.exited_with = exc_type
        return False


def _cl_sproc_row(*, status: str = "submitted"):
    return SimpleNamespace(
        Id=CL_ID,
        PublicId=CL_PID,
        RowVersion=b"\x00" * 8,
        CreatedDatetime="2026-01-01",
        ModifiedDatetime="2026-01-01",
        Status=status,
        VendorId=None,
        ProjectId=None,
        EmployeeName=None,
        JobName=None,
        WorkDate=None,
        TimeIn=None,
        TimeOut=None,
        BreakTime=None,
        RegularHours=None,
        OvertimeHours=None,
        TotalHours=None,
        HourlyRate=None,
        Markup=None,
        TotalAmount=None,
        SubCostCodeId=None,
        Description=None,
        BillingPeriodStart=None,
        BillLineItemId=None,
        BillVendorId=None,
        BillDate=None,
        DueDate=None,
        BillNumber=None,
        ImportBatchId=None,
        SourceFile=None,
        SourceRow=None,
        LineItemProjectIds=None,
        LineItemSubCostCodeIds=None,
    )


def _li_sproc_row(*, li_id: int, sub_cost_code_id=None):
    return SimpleNamespace(
        Id=li_id,
        PublicId=f"li-pub-{li_id}",
        RowVersion=b"\x00" * 8,
        CreatedDatetime="2026-01-01",
        ModifiedDatetime="2026-01-01",
        ContractLaborId=CL_ID,
        LineDate="2026-01-01",
        ProjectId=PROJECT_ID,
        SubCostCodeId=sub_cost_code_id,
        Description="d",
        Hours=1,
        Rate=1,
        Markup=0,
        Price=1,
        IsBillable=True,
        IsOverhead=False,
        BillLineItemId=None,
    )


def _real_chain(monkeypatch, *, fail_on=None, fail_on_nth_update=None):
    coded_lines = [
        _li_sproc_row(li_id=1, sub_cost_code_id=9001),
        _li_sproc_row(li_id=2, sub_cost_code_id=9001),
    ]
    rows = {
        "ReadCurrentReviewByContractLaborIdForUpdate": None,
        "ReadContractLaborById": _cl_sproc_row(status="submitted"),
        "ReadContractLaborLineItemsByContractLaborId": coded_lines,
        "UpdateContractLaborLineItemById": SimpleNamespace(
            Id=1,
            PublicId="li-pub",
            RowVersion=b"\x00" * 8,
            CreatedDatetime="2026-01-01",
            ModifiedDatetime="2026-01-01",
            ContractLaborId=CL_ID,
            LineDate="2026-01-01",
            ProjectId=PROJECT_ID,
            SubCostCodeId=9001,
            Description="d",
            Hours=1,
            Rate=1,
            Markup=0,
            Price=1,
            IsBillable=True,
            IsOverhead=False,
            BillLineItemId=None,
        ),
        "UpdateContractLaborAggregates": _cl_sproc_row(status="submitted"),
        "UpdateContractLaborById": _cl_sproc_row(status="ready"),
        "CreateReview": SimpleNamespace(
            Id=900,
            PublicId="rev-pub",
            RowVersion=b"\x00" * 8,
            CreatedDatetime="2026-01-01",
            ModifiedDatetime="2026-01-01",
            ReviewStatusId=30,
            UserId=REVIEWER_USER_ID,
            Comments=None,
            BillId=None,
            ExpenseId=None,
            BillCreditId=None,
            InvoiceId=None,
            ContractLaborId=CL_ID,
            EmailMessageId=888,
            IdempotencyKey=None,
            StatusName="Approved",
            StatusSortOrder=1,
            StatusIsFinal=True,
            StatusIsDeclined=False,
            ReviewKind="human",
            StatusIsInitial=False,
            StatusColor=None,
            UserFirstname="Pat",
            UserLastname="M",
        ),
    }
    decision_active = {"active": False}
    outer_conn = _FakeConn("decision-transaction", rows)
    outer_conn.decision_active = decision_active
    outer = _FakeCM(outer_conn, marks_decision_active=True)
    stray = []
    update_count = {"n": 0}

    def guarded_get_connection():
        if decision_active["active"]:
            cm = _FakeCM(_FakeConn("SEPARATE", rows))
            stray.append(cm)
            raise AssertionError(
                "repository opened its own connection inside the decision transaction"
            )
        return _FakeCM(_FakeConn("outside-decision", rows))

    calls = []

    def fake_call_procedure(*, cursor, name, params):
        cursor.last = name
        calls.append((name, cursor.conn.name))
        if name == "UpdateContractLaborLineItemById":
            update_count["n"] += 1
            if fail_on_nth_update is not None and update_count["n"] == fail_on_nth_update:
                raise RuntimeError(f"{name} failed")
        if fail_on is not None and name == fail_on:
            raise RuntimeError(f"{name} failed")

    monkeypatch.setattr(
        "entities.contract_labor.business.service.get_connection", lambda *a, **k: outer
    )
    for mod in (
        "entities.contract_labor.persistence.line_item_repo",
        "entities.contract_labor.persistence.repo",
        "entities.review.persistence.repo",
    ):
        monkeypatch.setattr(f"{mod}.get_connection", guarded_get_connection)
        monkeypatch.setattr(f"{mod}.call_procedure", fake_call_procedure)
    return outer, stray, calls


def _writes(calls, *, conn_name="decision-transaction"):
    return [
        c
        for c in calls
        if c[1] == conn_name
        and (
            c[0].startswith(("Update", "Create"))
            or c[0]
            in (
                "ReadContractLaborById",
                "ReadContractLaborLineItemsByContractLaborId",
            )
        )
    ]


@_patch_stack(CL_PATCH_TARGETS)
def test_cl_rejection_lock_then_create_on_one_conn(
    SccSvc,
    ProjSvc,
    LiRepo,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _ConnTracker()
    get_conn.return_value = tracker
    mock_cursor = MagicMock()
    tracker.conn.cursor.return_value = mock_cursor
    _wire_recipients(mock_cursor)

    svc = _cl_service_stub()
    ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=PROJECT_ID, public_id=PROJ_PID
    )
    _wire_review_status(RsSvc)
    ReviewRepo.return_value.read_current_by_contract_labor_id_for_update.return_value = None
    ReviewRepo.return_value.create.return_value = SimpleNamespace(
        id=900, review_status_id=100, status_is_final=False, status_is_declined=True
    )
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")

    with system_authz(), _u563_provenance_ok():
        svc._apply_decision_to_single_cl(
            contract_labor_public_id=CL_PID,
            project_public_id=PROJ_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    LiRepo.return_value.update_by_id.assert_not_called()
    svc.repo.update_aggregates.assert_not_called()
    ReviewRepo.return_value.read_current_by_contract_labor_id_for_update.assert_called_once()
    assert ReviewRepo.return_value.create.call_args.kwargs["conn"] is tracker.conn


def _wire_review_status(RsSvc, *, empty_approved: bool = False):
    if empty_approved:
        RsSvc.return_value.read_all.return_value = [
            SimpleNamespace(id=100, is_final=False, is_declined=True),
        ]
    else:
        RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")


@_patch_stack(CL_PATCH_TARGETS)
def test_cl_bad_status_raises_before_decision_get_connection(
    SccSvc,
    ProjSvc,
    LiRepo,
    RsSvc,
    ReviewRepo,
    get_conn,
):
    tracker = _ConnTracker()
    get_conn.return_value = tracker
    svc = _cl_service_stub()
    svc.read_by_public_id.return_value = SimpleNamespace(
        id=CL_ID, public_id=CL_PID, status="ready"
    )
    ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=PROJECT_ID, public_id=PROJ_PID
    )

    with system_authz():
        with pytest.raises(ValueError, match="no longer reviewable"):
            svc._apply_decision_to_single_cl(
                contract_labor_public_id=CL_PID,
                project_public_id=PROJ_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert tracker.enter_count == 0
    ReviewRepo.return_value.create.assert_not_called()


def test_cl_real_chain_approval_all_writes_share_transaction(monkeypatch):
    outer, stray, calls = _real_chain(monkeypatch)
    svc = ContractLaborService(repo=ContractLaborRepository())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=CL_ID, public_id=CL_PID, status="submitted")
    )

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        SimpleNamespace(ProjectId=PROJECT_ID, Email="pm@example.com", UserId=REVIEWER_USER_ID)
    ]
    auth_conn = MagicMock()
    auth_conn.cursor.return_value = mock_cursor
    auth_cm = MagicMock()
    auth_cm.__enter__ = MagicMock(return_value=auth_conn)
    auth_cm.__exit__ = MagicMock(return_value=False)

    decision_calls = {"n": 0}

    def get_connection_router():
        decision_calls["n"] += 1
        if decision_calls["n"] == 1:
            return auth_cm
        return outer

    monkeypatch.setattr(
        "entities.contract_labor.business.service.get_connection", get_connection_router
    )

    with patch("entities.contract_labor.business.service.ProjectService") as ProjSvc, \
         patch("entities.review_status.business.service.ReviewStatusService") as RsSvc, \
         patch("entities.sub_cost_code.business.service.SubCostCodeService") as SccSvc, \
         system_authz(), _u563_provenance_ok():
        ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=PROJECT_ID, public_id=PROJ_PID
        )
        _wire_review_status(RsSvc)
        SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        svc._expected_cl_reviewer_reply_conversation_id = MagicMock(
            return_value="cl-review-thread"
        )
        svc._apply_decision_to_single_cl(
            contract_labor_public_id=CL_PID,
            project_public_id=PROJ_PID,
            decision="approved",
            reviewer_email="pm@example.com",
            sub_cost_code_public_id=SCC_PID,
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    assert (
        "ReadCurrentReviewByContractLaborIdForUpdate",
        "decision-transaction",
    ) in calls, calls
    assert _writes(calls) == [
        ("CreateReview", "decision-transaction"),
        ("UpdateContractLaborLineItemById", "decision-transaction"),
        ("UpdateContractLaborLineItemById", "decision-transaction"),
        ("UpdateContractLaborAggregates", "decision-transaction"),
        ("ReadContractLaborById", "decision-transaction"),
        ("ReadContractLaborLineItemsByContractLaborId", "decision-transaction"),
        ("UpdateContractLaborById", "decision-transaction"),
    ], calls
    assert stray == [], "a repository opened its own connection inside the decision"
    assert outer.exited_with is None  # clean commit exit, not "never exited"


def test_cl_real_chain_second_line_failure_rolls_back_whole_member(monkeypatch):
    outer, stray, calls = _real_chain(monkeypatch, fail_on_nth_update=2)
    svc = ContractLaborService(repo=ContractLaborRepository())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=CL_ID, public_id=CL_PID, status="submitted")
    )
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        SimpleNamespace(ProjectId=PROJECT_ID, Email="pm@example.com", UserId=REVIEWER_USER_ID)
    ]
    auth_conn = MagicMock()
    auth_conn.cursor.return_value = mock_cursor
    auth_cm = MagicMock()
    auth_cm.__enter__ = MagicMock(return_value=auth_conn)
    auth_cm.__exit__ = MagicMock(return_value=False)
    decision_calls = {"n": 0}

    def get_connection_router():
        decision_calls["n"] += 1
        if decision_calls["n"] == 1:
            return auth_cm
        return outer

    monkeypatch.setattr(
        "entities.contract_labor.business.service.get_connection", get_connection_router
    )

    with patch("entities.contract_labor.business.service.ProjectService") as ProjSvc, \
         patch("entities.review_status.business.service.ReviewStatusService") as RsSvc, \
         patch("entities.sub_cost_code.business.service.SubCostCodeService") as SccSvc, \
         system_authz(), _u563_provenance_ok():
        ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=PROJECT_ID, public_id=PROJ_PID
        )
        _wire_review_status(RsSvc)
        SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        svc._expected_cl_reviewer_reply_conversation_id = MagicMock(
            return_value="cl-review-thread"
        )
        with pytest.raises(ValueError, match="line_item_id=2"):
            svc._apply_decision_to_single_cl(
                contract_labor_public_id=CL_PID,
                project_public_id=PROJ_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert "UpdateContractLaborAggregates" not in [c[0] for c in calls]
    assert outer.exited_with is not None and outer.exited_with != "not exited"


def test_cl_real_chain_aggregate_failure_rolls_back(monkeypatch):
    outer, stray, calls = _real_chain(monkeypatch, fail_on="UpdateContractLaborAggregates")
    svc = ContractLaborService(repo=ContractLaborRepository())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=CL_ID, public_id=CL_PID, status="submitted")
    )
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        SimpleNamespace(ProjectId=PROJECT_ID, Email="pm@example.com", UserId=REVIEWER_USER_ID)
    ]
    auth_conn = MagicMock()
    auth_conn.cursor.return_value = mock_cursor
    auth_cm = MagicMock()
    auth_cm.__enter__ = MagicMock(return_value=auth_conn)
    auth_cm.__exit__ = MagicMock(return_value=False)
    decision_calls = {"n": 0}

    def get_connection_router():
        decision_calls["n"] += 1
        if decision_calls["n"] == 1:
            return auth_cm
        return outer

    monkeypatch.setattr(
        "entities.contract_labor.business.service.get_connection", get_connection_router
    )

    with patch("entities.contract_labor.business.service.ProjectService") as ProjSvc, \
         patch("entities.review_status.business.service.ReviewStatusService") as RsSvc, \
         patch("entities.sub_cost_code.business.service.SubCostCodeService") as SccSvc, \
         system_authz(), _u563_provenance_ok():
        ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=PROJECT_ID, public_id=PROJ_PID
        )
        _wire_review_status(RsSvc)
        SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        svc._expected_cl_reviewer_reply_conversation_id = MagicMock(
            return_value="cl-review-thread"
        )
        with pytest.raises(Exception, match="UpdateContractLaborAggregates failed"):
            svc._apply_decision_to_single_cl(
                contract_labor_public_id=CL_PID,
                project_public_id=PROJ_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
                reviewer_email_message_public_id=REPLY_MSG_PUB,
            )

    assert "UpdateContractLaborById" not in [c[0] for c in calls]
    assert "ReadContractLaborById" not in [c[0] for c in calls]
    assert outer.exited_with is not None and outer.exited_with != "not exited"


def test_crew_wrapper_member_two_failure_leaves_member_one_applied(monkeypatch):
    svc = ContractLaborService(repo=MagicMock())
    svc.repo.read_reviewable_crew_by_project_and_date.return_value = [
        {"contract_labor_public_id": "cl-1", "employee_name": "Alice"},
        {"contract_labor_public_id": "cl-2", "employee_name": "Bob"},
    ]
    monkeypatch.setattr(
        "entities.contract_labor.business.service.ProjectService",
        lambda: SimpleNamespace(
            read_by_public_id=lambda **kw: SimpleNamespace(id=PROJECT_ID, public_id=PROJ_PID),
        ),
    )
    monkeypatch.setattr(
        "shared.authz.provenance.bind_reviewer_reply_to_thread",
        lambda **kw: SimpleNamespace(id=1),
    )
    svc._expected_cl_reviewer_reply_conversation_id = MagicMock(return_value="conv")

    outcomes = []

    def single(**kw):
        pub = kw["contract_labor_public_id"]
        if pub == "cl-2":
            raise ValueError("member 2 boom")
        outcomes.append(pub)
        return {
            "decision_applied": "approved",
            "review_status": "Approved",
            "contract_labor_public_id": pub,
            "ready_flipped": False,
        }

    monkeypatch.setattr(svc, "_apply_decision_to_single_cl", single)

    result = svc.apply_reviewer_decision(
        project_public_id=PROJ_PID,
        work_date="2026-01-01",
        decision="approved",
        reviewer_email="pm@example.com",
        sub_cost_code_public_id=SCC_PID,
    )

    assert outcomes == ["cl-1"]
    assert result["applied_count"] == 1
    assert len(result["failures"]) == 1
    assert "cl-2" in result["failures"][0]


OTHER_PROJECT_ID = 11


class _DualSnapshotLineItemRepo:
    """Pre-lock read (no conn) vs in-transaction read (conn=) for ready-flip tests."""

    def __init__(self, *, pre_lock, in_transaction):
        self.pre_lock = list(pre_lock)
        self.in_transaction = list(in_transaction)

    def read_by_contract_labor_id(self, contract_labor_id, *, conn=None):
        if conn is not None:
            return list(self.in_transaction)
        return list(self.pre_lock)

    def update_by_id(self, *, id, conn=None, **fields):
        target = next(li for li in self.pre_lock if li.id == id)
        for key, value in fields.items():
            if key == "row_version":
                continue
            setattr(target, key, value)
        return target


def _ready_flip_harness(LiRepo, *, pre_lock, in_transaction):
    dual = _DualSnapshotLineItemRepo(pre_lock=pre_lock, in_transaction=in_transaction)
    LiRepo.return_value = dual
    svc = _cl_service_stub()
    svc.mark_as_ready_via_review_approval = ContractLaborService(
        repo=MagicMock()
    ).mark_as_ready_via_review_approval.__get__(svc, ContractLaborService)
    svc.repo.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=CL_ID, status="submitted")
    )
    svc.repo.update_by_id = MagicMock(
        side_effect=lambda cl, **kw: SimpleNamespace(id=CL_ID, status=cl.status)
    )
    return svc, dual


@_patch_stack(CL_PATCH_TARGETS)
def test_ready_flip_uses_in_transaction_read_when_pre_lock_still_uncoded(
    SccSvc, ProjSvc, LiRepo, RsSvc, ReviewRepo, get_conn,
):
    """Concurrent coding on another project: stale pre-lock misses it; txn read is coded."""
    tracker = _ConnTracker()
    get_conn.return_value = tracker
    mock_cursor = MagicMock()
    tracker.conn.cursor.return_value = mock_cursor
    _wire_recipients(mock_cursor)

    pre_lock = [
        _cl_line(li_id=1, project_id=PROJECT_ID),
        _cl_line(li_id=2, project_id=OTHER_PROJECT_ID, sub_cost_code_id=None),
    ]
    in_txn = [
        _cl_line(li_id=1, project_id=PROJECT_ID, sub_cost_code_id=9001),
        _cl_line(li_id=2, project_id=OTHER_PROJECT_ID, sub_cost_code_id=9001),
    ]
    svc, _dual = _ready_flip_harness(LiRepo, pre_lock=pre_lock, in_transaction=in_txn)

    ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=PROJECT_ID, public_id=PROJ_PID
    )
    _wire_review_status(RsSvc)
    ReviewRepo.return_value.read_current_by_contract_labor_id_for_update.return_value = None
    ReviewRepo.return_value.create.return_value = SimpleNamespace(
        id=900,
        review_status_id=30,
        status_is_final=True,
        status_is_declined=False,
    )
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)

    with system_authz(), _u563_provenance_ok():
        result = svc._apply_decision_to_single_cl(
            contract_labor_public_id=CL_PID,
            project_public_id=PROJ_PID,
            decision="approved",
            reviewer_email="pm@example.com",
            sub_cost_code_public_id=SCC_PID,
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    assert result["ready_flipped"] is True
    svc.repo.update_by_id.assert_called_once()


@_patch_stack(CL_PATCH_TARGETS)
def test_ready_flip_defers_when_in_transaction_read_still_uncoded(
    SccSvc, ProjSvc, LiRepo, RsSvc, ReviewRepo, get_conn,
):
    """Pre-lock looked fully coded; in-transaction re-read sees an uncoded project line."""
    tracker = _ConnTracker()
    get_conn.return_value = tracker
    mock_cursor = MagicMock()
    tracker.conn.cursor.return_value = mock_cursor
    _wire_recipients(mock_cursor)

    pre_lock = [
        _cl_line(li_id=1, project_id=PROJECT_ID, sub_cost_code_id=9001),
        _cl_line(li_id=2, project_id=OTHER_PROJECT_ID, sub_cost_code_id=9001),
    ]
    in_txn = [
        _cl_line(li_id=1, project_id=PROJECT_ID, sub_cost_code_id=9001),
        _cl_line(li_id=2, project_id=OTHER_PROJECT_ID, sub_cost_code_id=None),
    ]
    svc, _dual = _ready_flip_harness(LiRepo, pre_lock=pre_lock, in_transaction=in_txn)

    ProjSvc.return_value.read_by_public_id.return_value = SimpleNamespace(
        id=PROJECT_ID, public_id=PROJ_PID
    )
    _wire_review_status(RsSvc)
    ReviewRepo.return_value.read_current_by_contract_labor_id_for_update.return_value = None
    ReviewRepo.return_value.create.return_value = SimpleNamespace(
        id=900,
        review_status_id=30,
        status_is_final=True,
        status_is_declined=False,
    )
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)

    with system_authz(), _u563_provenance_ok():
        result = svc._apply_decision_to_single_cl(
            contract_labor_public_id=CL_PID,
            project_public_id=PROJ_PID,
            decision="approved",
            reviewer_email="pm@example.com",
            sub_cost_code_public_id=SCC_PID,
            reviewer_email_message_public_id=REPLY_MSG_PUB,
        )

    assert result["ready_flipped"] is False
    svc.repo.update_by_id.assert_not_called()


def test_mark_as_ready_without_conn_still_opens_own_connection(monkeypatch):
    """Callers outside a transaction keep the pre-U-564b read path."""
    reads = {"conn": []}

    class _LiRepo:
        def read_by_contract_labor_id(self, contract_labor_id, *, conn=None):
            reads["conn"].append(conn)
            return [_cl_line(li_id=1, project_id=PROJECT_ID, sub_cost_code_id=9001)]

    parent = SimpleNamespace(id=CL_ID, status="submitted")
    repo = MagicMock()
    repo.read_by_id.return_value = parent
    repo.update_by_id.side_effect = lambda cl, **kw: cl

    svc = ContractLaborService(repo=repo)
    monkeypatch.setattr(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository",
        _LiRepo,
    )

    out = svc.mark_as_ready_via_review_approval(contract_labor_id=CL_ID)

    assert reads["conn"] == [None]
    assert out.status == "ready"
