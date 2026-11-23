"""U-797b2: a bare 'Approved' applies the suggestions shown in the email (flag on), and nothing else changes."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from entities.contract_labor.business import service as svc
from entities.contract_labor.business.service import ContractLaborService
from entities.review.business import cl_notification_service as cns_module
from entities.review.business.cl_notification_service import ContractLaborReviewNotificationService
from entities.review.business.review_notification_service import (
    CrewTargetSnapshot,
    cl_lines_fingerprint,
)
from shared.authz import provenance as prov
from shared.lifecycle.completion_gate import HoldingCodeRefused


BARE_REFUSAL = (
    "A bare 'Approved' applies the suggested codes only on a reply to the suggestions email; "
    "reply with a sub cost code instead."
)
REQUIRED = "sub_cost_code_public_id is required when decision='approved'."
REASON_NOT_IN_EMAIL = "not in the email"
REASON_NO_SUGGESTION = "no suggestion shown"
REASON_SPLIT = "possible split — name a code"
REASON_CHANGED = "lines changed since the email"


class FakeProject:
    def read_by_public_id(self, public_id):
        return SimpleNamespace(id=9, public_id=public_id)


def _snap(cl_id, *, code=77, sid=5, source="history", may_split=False, fp="fp-ok"):
    return CrewTargetSnapshot(
        contract_labor_id=cl_id,
        suggested_sub_cost_code_id=code,
        suggestion_id=sid,
        suggestion_source=source,
        suggestion_may_split=may_split,
        lines_fingerprint=fp,
    )


def _member(cl_id, name):
    return {"contract_labor_id": cl_id, "contract_labor_public_id": f"cl-{name.lower()}", "employee_name": name}


class FakeRepo:
    def __init__(self, st):
        self.st = st

    def read_reviewable_crew_by_project_and_date(self, *, project_id, work_date):
        return list(self.st.crew)

    def update_aggregates(self, *, id, conn=None):
        pass


@pytest.fixture
def orch(monkeypatch):
    st = SimpleNamespace(
        flag=True,
        bare_notif=SimpleNamespace(Id=99),
        bound_notif_for_bare=True,
        legacy_bound=True,
        snaps={},
        crew=[],
        behave={},
        apply_calls=[],
        bind_cl_calls=[],
        snapshot_reads=[],
        notif_calls=[],
        once_calls=[],
        settings_loads=0,
    )

    def fake_flag(settings=None):
        st.settings_loads += 1
        return st.flag

    monkeypatch.setattr(cns_module, "cl_suggestions_in_review_enabled", fake_flag)
    monkeypatch.setattr(svc, "ProjectService", FakeProject)

    em = SimpleNamespace(id=7, conversation_id="Conv-B", subject="Crew", public_id="EM7")

    def fake_once(**kw):
        st.once_calls.append(kw)
        return em if st.legacy_bound else None

    def fake_notif_once(**kw):
        st.notif_calls.append(kw)
        if not st.bound_notif_for_bare:
            return None
        return (em, st.bare_notif)

    monkeypatch.setattr(prov, "resolve_reply_once", fake_once)
    monkeypatch.setattr(prov, "resolve_reply_notification_once", fake_notif_once)

    def fake_read_snapshots(self, notification_id):
        st.snapshot_reads.append(notification_id)
        return dict(st.snaps)

    from entities.review.business.review_notification_service import ReviewNotificationRecordService

    monkeypatch.setattr(ReviewNotificationRecordService, "read_crew_snapshots", fake_read_snapshots)

    def fake_apply(self, **kw):
        st.apply_calls.append(kw)
        pid = kw["contract_labor_public_id"]
        behave = st.behave.get(pid)
        if isinstance(behave, BaseException):
            raise behave
        if behave is not None:
            return behave
        if kw["decision"] == "approved" and not kw.get("sub_cost_code_public_id") and kw.get("suggestion") is None:
            raise ValueError(REQUIRED)
        return {
            "decision_applied": kw["decision"],
            "review_status": "Approved",
            "contract_labor_public_id": pid,
            "ready_flipped": False,
        }

    monkeypatch.setattr(ContractLaborService, "_apply_decision_to_single_cl", fake_apply)

    def fake_bind_cl(self, **kw):
        st.bind_cl_calls.append(kw)
        return SimpleNamespace(id=500)

    monkeypatch.setattr(ContractLaborService, "_bind_cl_decision_reply", fake_bind_cl)

    service = ContractLaborService(repo=FakeRepo(st))
    return service, st


def _call(service, *, decision="approved", sub=None):
    return service.apply_reviewer_decision(
        project_public_id="proj-1",
        work_date="2026-11-19",
        decision=decision,
        reviewer_email="pm@x.test",
        sub_cost_code_public_id=sub,
        reviewer_email_message_public_id="EM7",
    )


def test_flag_off_bare_approved_all_fail_with_required_message(orch):
    service, st = orch
    st.flag = False
    st.crew = [_member(1, "Ann")]
    with pytest.raises(ValueError, match="sub_cost_code_public_id is required"):
        _call(service)
    assert st.snapshot_reads == []
    assert st.notif_calls == []
    assert all(c["suggestion"] is None for c in st.apply_calls)


def test_named_code_with_flag_on_is_named_mode_and_never_reads_snapshots(orch):
    service, st = orch
    st.crew = [_member(1, "Ann"), _member(2, "Ben")]
    st.snaps = {1: _snap(1)}
    result = _call(service, sub="scc-named")
    assert result["mode"] == "named_code"
    assert result["not_applied"] == []
    assert result["applied_count"] == 2
    assert st.snapshot_reads == []
    assert st.notif_calls == []
    assert all(c["sub_cost_code_public_id"] == "scc-named" for c in st.apply_calls)
    assert all(c["suggestion"] is None for c in st.apply_calls)


def test_bare_approved_binder_none_raises_with_zero_writes_and_no_legacy_binding(orch):
    service, st = orch
    st.bound_notif_for_bare = False
    st.crew = [_member(1, "Ann")]
    st.snaps = {1: _snap(1)}
    with pytest.raises(ValueError) as exc:
        _call(service)
    assert str(exc.value) == BARE_REFUSAL
    assert st.apply_calls == []
    assert st.bind_cl_calls == []
    assert st.snapshot_reads == []


def test_bare_approved_empty_snapshots_raises_with_zero_writes(orch):
    service, st = orch
    st.crew = [_member(1, "Ann"), _member(2, "Ben")]
    st.snaps = {}
    with pytest.raises(ValueError):
        _call(service)
    assert st.apply_calls == []
    assert st.snapshot_reads == [99]


def test_bare_approved_mixed_crew_applies_only_clean_with_exact_reasons(orch):
    service, st = orch
    st.crew = [
        _member(1, "Ann"),   # clean
        _member(2, "Ben"),   # split
        _member(3, "Cal"),   # no suggestion (code NULL)
        _member(4, "Dee"),   # not in the email
        _member(5, "Eve"),   # clean snapshot, lines changed at apply
        _member(6, "Fay"),   # may_split None
        _member(7, "Gus"),   # source "rules"
    ]
    st.snaps = {
        1: _snap(1, code=77),
        2: _snap(2, may_split=True),
        3: _snap(3, code=None, sid=None, source=None, fp="fp-3"),
        5: _snap(5, code=88),
        6: _snap(6, may_split=None),
        7: _snap(7, source="rules"),
    }
    from entities.contract_labor.business.service import _SuggestionNotApplied

    st.behave["cl-eve"] = _SuggestionNotApplied("lines changed", REASON_CHANGED)

    result = _call(service)

    applied_ids = [c["contract_labor_public_id"] for c in st.apply_calls]
    assert applied_ids == ["cl-ann", "cl-eve"]
    ann_call = st.apply_calls[0]
    assert ann_call["suggestion"].suggested_sub_cost_code_id == 77
    assert ann_call["suggestion"].lines_fingerprint == "fp-ok"
    assert st.apply_calls[1]["suggestion"].suggested_sub_cost_code_id == 88

    assert result["mode"] == "suggested"
    assert result["applied_count"] == 1
    assert result["failures"] == []
    assert result["not_applied"] == [
        {"contract_labor_public_id": "cl-ben", "employee_name": "Ben", "reason": REASON_SPLIT},
        {"contract_labor_public_id": "cl-cal", "employee_name": "Cal", "reason": REASON_NO_SUGGESTION},
        {"contract_labor_public_id": "cl-dee", "employee_name": "Dee", "reason": REASON_NOT_IN_EMAIL},
        {"contract_labor_public_id": "cl-fay", "employee_name": "Fay", "reason": REASON_NO_SUGGESTION},
        {"contract_labor_public_id": "cl-gus", "employee_name": "Gus", "reason": REASON_NO_SUGGESTION},
        {"contract_labor_public_id": "cl-eve", "employee_name": "Eve", "reason": REASON_CHANGED},
    ]


def test_holding_suggested_code_is_per_member_failure_others_apply(orch):
    service, st = orch
    st.crew = [_member(1, "Ann"), _member(2, "Ben")]
    st.snaps = {1: _snap(1, code=77), 2: _snap(2, code=78)}
    st.behave["cl-ann"] = HoldingCodeRefused("suspense")
    result = _call(service)
    assert result["applied_count"] == 1
    assert result["applied"][0]["contract_labor_public_id"] == "cl-ben"
    assert len(result["failures"]) == 1
    assert "cl-ann" in result["failures"][0]
    assert result["not_applied"] == []


def test_named_mode_holding_code_still_reraises(orch):
    service, st = orch
    st.crew = [_member(1, "Ann")]
    st.behave["cl-ann"] = HoldingCodeRefused("suspense")
    with pytest.raises(HoldingCodeRefused):
        _call(service, sub="scc-holding")


def test_all_ineligible_raises_with_reasons_and_zero_writes(orch):
    service, st = orch
    st.crew = [_member(1, "Ann"), _member(2, "Ben")]
    st.snaps = {1: _snap(1, may_split=True)}
    with pytest.raises(ValueError) as exc:
        _call(service)
    msg = str(exc.value)
    assert REASON_SPLIT in msg
    assert REASON_NOT_IN_EMAIL in msg
    assert [c["contract_labor_public_id"] for c in st.apply_calls] == []


def test_rejected_is_unaffected_and_never_reads_snapshots(orch):
    service, st = orch
    st.crew = [_member(1, "Ann")]
    st.snaps = {1: _snap(1)}
    result = _call(service, decision="rejected")
    assert result["applied_count"] == 1
    assert result["not_applied"] == []
    assert st.snapshot_reads == []
    assert st.notif_calls == []
    assert st.apply_calls[0]["suggestion"] is None


def test_may_split_none_is_not_clean(orch):
    service, st = orch
    st.crew = [_member(1, "Ann")]
    st.snaps = {1: _snap(1, may_split=None)}
    with pytest.raises(ValueError) as exc:
        _call(service)
    assert REASON_NO_SUGGESTION in str(exc.value)
    assert st.apply_calls == []


def test_source_rules_is_not_clean(orch):
    service, st = orch
    st.crew = [_member(1, "Ann")]
    st.snaps = {1: _snap(1, source="rules")}
    with pytest.raises(ValueError) as exc:
        _call(service)
    assert REASON_NO_SUGGESTION in str(exc.value)
    assert st.apply_calls == []


# ── _apply_decision_to_single_cl: suggestion-mode internals ───────────────────

class _Conn:
    def __init__(self, recipients):
        self.enters = 0
        self.conn = MagicMock(name="conn")
        self.conn.cursor.return_value.fetchall.return_value = recipients

    def __call__(self):
        return self

    def __enter__(self):
        self.enters += 1
        return self.conn

    def __exit__(self, *exc):
        return False


@pytest.fixture
def deep(monkeypatch):
    writes: list = []
    st = SimpleNamespace(writes=writes, scc_reads=[], holding_ids={78}, missing_ids={404})
    lines = [
        SimpleNamespace(id=11, project_id=9, line_date="2026-11-19", description="d", hours=Decimal("8"),
                        rate=Decimal("25"), markup=Decimal("0"), price=Decimal("200"), is_billable=True,
                        is_overhead=False, bill_line_item_id=None, row_version_bytes=b"x", sub_cost_code_id=None),
        SimpleNamespace(id=12, project_id=8, line_date="2026-11-19", description="o", hours=Decimal("3"),
                        rate=Decimal("25"), markup=Decimal("0"), price=Decimal("75"), is_billable=True,
                        is_overhead=False, bill_line_item_id=None, row_version_bytes=b"y", sub_cost_code_id=None),
    ]
    st.lines = lines
    conn = _Conn([SimpleNamespace(ProjectId=9, Email="pm@x.test", UserId=20)])
    monkeypatch.setattr(svc, "get_connection", conn)
    monkeypatch.setattr(svc, "call_procedure", lambda **kw: None)
    monkeypatch.setattr(svc, "assert_may_act_as", lambda **kw: False)
    monkeypatch.setattr(svc, "ProjectService", FakeProject)
    monkeypatch.setattr(ContractLaborService, "_bind_cl_decision_reply", lambda self, **kw: SimpleNamespace(id=500))
    monkeypatch.setattr(
        ContractLaborService, "read_by_public_id",
        lambda self, public_id: SimpleNamespace(id=50, status="pending_review", work_date="2026-11-19"),
    )
    monkeypatch.setattr(ContractLaborService, "mark_as_ready_via_review_approval", lambda self, **kw: None)

    class FakeLiRepo:
        def read_by_contract_labor_id(self, contract_labor_id):
            return list(st.lines)

        def update_by_id(self, **kw):
            writes.append(f"line:{kw['id']}")

    class FakeReviewRepo:
        def read_current_by_contract_labor_id_for_update(self, cl_id, conn=None):
            return None

        def create(self, **kw):
            writes.append("review")
            return SimpleNamespace(review_status_id=30, status_is_final=True, status_is_declined=False)

    class FakeRS:
        def read_all(self):
            return [
                SimpleNamespace(id=100, name="Declined", is_final=False, is_declined=True),
                SimpleNamespace(id=30, name="Approved", is_final=True, is_declined=False),
            ]

        def read_by_id(self, id):
            return SimpleNamespace(name="Approved")

    class FakeSCC:
        def read_by_id(self, id):
            st.scc_reads.append(id)
            if id in st.missing_ids:
                return None
            return SimpleNamespace(id=id, public_id=f"scc-{id}", is_holding_code=id in st.holding_ids)

        def read_by_public_id(self, public_id):
            if public_id == "scc-77":
                return SimpleNamespace(id=77, public_id=public_id, is_holding_code=False)
            return None

    monkeypatch.setattr("entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository", FakeLiRepo)
    monkeypatch.setattr("entities.review.persistence.repo.ReviewRepository", FakeReviewRepo)
    monkeypatch.setattr("entities.review_status.business.service.ReviewStatusService", FakeRS)
    monkeypatch.setattr("entities.sub_cost_code.business.service.SubCostCodeService", FakeSCC)

    service = ContractLaborService(repo=SimpleNamespace(update_aggregates=lambda **kw: writes.append("aggregates")))
    return service, st, conn


def _apply_one(service, **kw):
    return service._apply_decision_to_single_cl(
        contract_labor_public_id="cl-ann",
        project_public_id="proj-1",
        decision="approved",
        reviewer_email="pm@x.test",
        reviewer_email_message_public_id="EM7",
        **kw,
    )


def test_matching_fingerprint_applies_with_snapshot_code(deep):
    service, st, _ = deep
    fp = cl_lines_fingerprint([(11, Decimal("8"))])
    result = _apply_one(service, suggestion=_snap(50, code=77, fp=fp))
    assert result["contract_labor_id"] == 50
    assert st.scc_reads == [77]
    assert "line:11" in st.writes and "review" in st.writes
    assert "line:12" not in st.writes


def test_changed_fingerprint_refuses_before_any_write(deep):
    from entities.contract_labor.business.service import _SuggestionNotApplied

    service, st, _ = deep
    fp = cl_lines_fingerprint([(11, Decimal("7"))])
    with pytest.raises(_SuggestionNotApplied) as exc:
        _apply_one(service, suggestion=_snap(50, code=77, fp=fp))
    assert exc.value.reason == REASON_CHANGED
    assert st.writes == []


def test_lines_changed_is_a_valueerror_subclass(deep):
    from entities.contract_labor.business.service import _SuggestionNotApplied

    assert issubclass(_SuggestionNotApplied, ValueError)


def test_sub_cost_code_id_not_found_raises_with_zero_writes(deep):
    service, st, _ = deep
    with pytest.raises(ValueError):
        _apply_one(service, suggestion=_snap(50, code=404))
    assert st.writes == []


def test_sub_cost_code_id_holding_raises_holding_refused(deep):
    service, st, _ = deep
    with pytest.raises(HoldingCodeRefused):
        _apply_one(service, suggestion=_snap(50, code=78))
    assert st.writes == []


def test_no_code_at_all_keeps_today_required_error(deep):
    service, st, _ = deep
    with pytest.raises(ValueError, match="sub_cost_code_public_id is required"):
        _apply_one(service)
    assert st.writes == []


# ── Fix round 1 ───────────────────────────────────────────────────────────────

ALREADY_CODED_REASON = "already coded — name a code to change it"


def test_f1_coded_matched_line_refuses_before_any_write(deep):
    service, st, _ = deep
    from entities.contract_labor.business.service import _SuggestionNotApplied

    st.lines[0].sub_cost_code_id = 55
    fp = cl_lines_fingerprint([(11, Decimal("8"))])
    with pytest.raises(_SuggestionNotApplied) as exc:
        _apply_one(service, suggestion=_snap(50, code=77, fp=fp))
    assert exc.value.reason == ALREADY_CODED_REASON
    assert st.writes == []


def test_f1_already_coded_is_a_valueerror(deep):
    from entities.contract_labor.business.service import _SuggestionNotApplied

    assert issubclass(_SuggestionNotApplied, ValueError)


def test_f1_named_mode_still_recodes_coded_lines(deep):
    service, st, _ = deep
    st.lines[0].sub_cost_code_id = 55
    _apply_one(service, sub_cost_code_public_id="scc-77")
    assert "line:11" in st.writes and "review" in st.writes


def test_f1_member_with_coded_line_is_not_applied_rest_apply(orch):
    service, st = orch
    from entities.contract_labor.business.service import _SuggestionNotApplied

    st.crew = [_member(1, "Ann"), _member(2, "Ben")]
    st.snaps = {1: _snap(1, code=77), 2: _snap(2, code=78)}
    st.behave["cl-ann"] = _SuggestionNotApplied("coded", ALREADY_CODED_REASON)
    result = _call(service)
    assert result["applied_count"] == 1
    assert result["applied"][0]["contract_labor_public_id"] == "cl-ben"
    assert result["failures"] == []
    assert result["not_applied"] == [
        {"contract_labor_public_id": "cl-ann", "employee_name": "Ann", "reason": ALREADY_CODED_REASON},
    ]


# F4: hours normalize to two decimals, so equal hours fingerprint the same.

@pytest.mark.parametrize("value", [8, "8.0", Decimal("8.00"), 8.0, Decimal("8"), "8.00"])
def test_f4_equal_hours_fingerprint_the_same(value):
    from entities.review.business import review_notification_service as rns

    assert rns.cl_lines_fingerprint([(1, value)]) == rns.cl_lines_fingerprint([(1, Decimal("8.00"))])


def test_f4_missing_hours_is_0_00():
    import hashlib
    from entities.review.business import review_notification_service as rns

    expected = hashlib.sha256("9:0.00".encode("utf-8")).hexdigest()
    assert rns.cl_lines_fingerprint([(9, None)]) == expected
    assert rns.cl_lines_fingerprint([(9, None)]) == rns.cl_lines_fingerprint([(9, Decimal("0.00"))])


# F5: coverage for the eligibility, parity, legacy-binding and settings paths.

def test_f5a_shown_code_with_no_fingerprint_is_no_suggestion(orch):
    service, st = orch
    st.crew = [_member(1, "Ann")]
    st.snaps = {1: _snap(1, code=77, fp=None)}
    with pytest.raises(ValueError) as exc:
        _call(service)
    assert REASON_NO_SUGGESTION in str(exc.value)
    assert st.apply_calls == []


def test_f5b_crew_snapshot_fingerprint_matches_apply_side_fingerprint():
    from entities.review.business import cl_notification_service as cns
    from entities.review.business.review_notification_service import cl_lines_fingerprint

    rows = [
        SimpleNamespace(ContractLaborId=1, LineItemId=10, Hours=Decimal("2.50")),
        SimpleNamespace(ContractLaborId=1, LineItemId=11, Hours=Decimal("3.00")),
    ]
    displays = {1: SimpleNamespace(code_id=77, suggestion_id=5, source="history", split_shown=False)}
    snap = cns._crew_snapshots(rows, displays)[1]
    repo_lines = [
        SimpleNamespace(id=10, hours=Decimal("2.50"), project_id=9),
        SimpleNamespace(id=11, hours=Decimal("3.00"), project_id=9),
        SimpleNamespace(id=12, hours=Decimal("1.00"), project_id=8),
    ]
    apply_side = cl_lines_fingerprint([(li.id, li.hours) for li in repo_lines if li.project_id == 9])
    assert snap.lines_fingerprint == apply_side


def test_f5c_successful_suggestion_run_never_uses_per_member_legacy_binding(orch):
    service, st = orch
    st.crew = [_member(1, "Ann"), _member(2, "Ben")]
    st.snaps = {1: _snap(1, code=77), 2: _snap(2, code=78)}
    result = _call(service)
    assert result["applied_count"] == 2
    assert st.bind_cl_calls == []
    assert not any(c.get("legacy_only") for c in st.bind_cl_calls)


def test_f5d_named_code_path_never_loads_settings(orch):
    service, st = orch
    st.crew = [_member(1, "Ann")]
    _call(service, sub="scc-named")
    assert st.settings_loads == 0


# F2/F3: the user-facing text.

def test_f2_tool_description_names_both_error_shapes():
    text = Path(
        "entities/contract_labor/intelligence/tools.py"
    ).read_text(encoding="utf-8")
    assert "applied none of the N crew member(s)" in text
    assert "failed for all N crew member(s)" in text
    assert "not an authorized reviewer" in text


def test_f3_prompt_bare_approval_requires_nothing_beyond_the_approval():
    text = Path(
        "intelligence/agents/contract_labor_specialist/prompt.md"
    ).read_text(encoding="utf-8")
    assert "nothing beyond the approval" in text
    assert "flag it for human review" in text
    assert "per-member corrections are not supported yet" in text


# ── EM round: mutation survivors (M08, M12, M20) ─────────────────────────────

def test_m08_one_coded_line_among_several_refuses_the_member(deep):
    """A member is skipped when ANY of its project lines is coded, not only when all are."""
    service, st, _ = deep
    from entities.contract_labor.business.service import _SuggestionNotApplied

    st.lines.append(SimpleNamespace(
        id=13, project_id=9, line_date="2026-11-19", description="d2", hours=Decimal("2"),
        rate=Decimal("25"), markup=Decimal("0"), price=Decimal("50"), is_billable=True,
        is_overhead=False, bill_line_item_id=None, row_version_bytes=b"z", sub_cost_code_id=None))
    st.lines[0].sub_cost_code_id = 55
    fp = cl_lines_fingerprint([(11, Decimal("8")), (13, Decimal("2"))])
    with pytest.raises(_SuggestionNotApplied) as exc:
        _apply_one(service, suggestion=_snap(50, code=77, fp=fp))
    assert exc.value.reason == ALREADY_CODED_REASON
    assert st.writes == []


def test_m12_empty_snapshots_refusal_names_the_missing_suggestions(orch):
    service, st = orch
    st.crew = [_member(1, "Ann")]
    st.snaps = {}
    with pytest.raises(ValueError) as exc:
        _call(service)
    assert "recorded no per-member suggestions" in str(exc.value)
    assert st.apply_calls == []


def test_m20_flag_reads_off_when_settings_cannot_load(monkeypatch):
    import config
    from entities.review.business import cl_notification_service as cln

    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(config, "Settings", boom)
    assert cln.cl_suggestions_in_review_enabled() is False
