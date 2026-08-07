"""Unit tests for consolidated ContractLabor review-notification + crew apply.

Pure/DB-free coverage: HTML body building, the work-date gate, claim/dedup
with release-on-failure, crew-wide apply_reviewer_decision, the conditional
ready-flip invariant (choke point + delegation), and structural pins on
`dbo.contract_labor.sql`.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import types

from entities.contract_labor.business.service import ContractLaborService
from entities.review.business.cl_notification_service import (
    ContractLaborReviewNotificationService,
)
from tests.test_sproc_nocount_shape_guard import strip_comments_and_strings
from tests.test_sproc_single_source import _sproc_body


def _submitted_line(
    cl_id,
    name,
    project_id,
    *,
    abbr="",
    hours=8.0,
    billable=True,
    overhead=False,
    desc="work",
):
    return types.SimpleNamespace(
        ContractLaborId=cl_id,
        EmployeeName=name,
        ProjectId=project_id,
        ProjectName="",
        ProjectAbbreviation=abbr,
        Hours=hours,
        IsBillable=billable,
        IsOverhead=overhead,
        Description=desc,
    )


def test_build_body_combines_all_laborers():
    svc = ContractLaborReviewNotificationService()
    lines = [
        _submitted_line(1, "Brayan", 0, hours=8.0, billable=True, overhead=False, desc="Framing"),
        _submitted_line(2, "Elmer", 0, hours=8.0, billable=True, overhead=False, desc="Framing"),
        _submitted_line(3, "Wilmer", 0, hours=4.0, billable=True, overhead=False, desc="Cleanup"),
    ]
    body = svc._build_body(
        project_label="TB3",
        work_date="2026-07-15",
        lines=lines,
        pms=[{"firstname": "Cassidy", "lastname": "X", "email": "c@x.com"}],
    )
    # Greeting + crew-wide ask carrying project + date.
    assert "Cassidy," in body
    assert "TB3" in body and "2026-07-15" in body
    assert "applied to the full crew" in body
    # Exactly one bold header per distinct laborer — the whole crew combined.
    for name in ("Brayan", "Elmer", "Wilmer"):
        assert f"<b>{name}</b>" in body
    assert body.count("<b>") == 3


def test_build_body_groups_multiple_lines_per_worker():
    svc = ContractLaborReviewNotificationService()
    lines = [
        _submitted_line(1, "Mac", 0, hours=5.0, billable=True, overhead=False, desc="HA"),
        _submitted_line(1, "Mac", 0, hours=3.0, billable=True, overhead=False, desc="TB3 punch"),
    ]
    body = svc._build_body(
        project_label="HA", work_date="2026-06-16", lines=lines, pms=[],
    )
    # One worker header, both of the worker's lines under it.
    assert body.count("<b>Mac</b>") == 1
    assert body.count("Hours:") == 2
    # No PMs → no salutation paragraph; body opens straight at the ask.
    assert body.startswith("<p>The following Contract Labor for HA")


def test_build_body_escapes_and_applies_defaults():
    svc = ContractLaborReviewNotificationService()
    lines = [_submitted_line(1, "A & B", 0, hours=None, billable=None, overhead=None, desc=None)]
    body = svc._build_body(
        project_label="P<1>", work_date="2026-07-15", lines=lines, pms=[],
    )
    assert "A &amp; B" in body          # name HTML-escaped
    assert "P&lt;1&gt;" in body          # project label HTML-escaped
    assert "(no description)" in body    # None description default
    assert "Hours: 0.00" in body         # None hours default
    assert "Is Billable: Yes" in body    # None billable → default True
    assert "Is Overhead: No" in body     # None overhead → default False


def test_enqueue_drafts_short_circuits_without_work_date():
    """No work_date → no DB access, no raise (failure-isolated public surface)."""
    svc = ContractLaborReviewNotificationService()
    cl = types.SimpleNamespace(public_id="cl-x", work_date=None)
    svc.enqueue_drafts(contract_labor=cl)  # must return cleanly


# ── Shared fixtures for enqueue / gate / claim tests ─────────────────────


def _notification_svc_with_patches(monkeypatch):
    """Fresh service with BCC lookup stubbed (no Settings)."""
    svc = ContractLaborReviewNotificationService()
    monkeypatch.setattr(svc, "_build_bcc_addresses", lambda: [])
    return svc


def _cl_trigger(*, work_date="2026-07-15"):
    return types.SimpleNamespace(
        public_id="cl-trigger",
        work_date=date.fromisoformat(work_date)
        if isinstance(work_date, str)
        else work_date,
    )


# === A. The date gate ===


def test_date_gate_holds_when_pending_count_positive_no_claim_or_enqueue(
    monkeypatch,
):
    svc = _notification_svc_with_patches(monkeypatch)
    claim_calls = []
    enqueue_calls = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 3)
    # Supply a NON-EMPTY crew: if the gate wrongly releases, the loop reaches
    # claim+enqueue and this test reddens. Without this the real DB-backed
    # reader would be hit, fail, return [], and the run would short-circuit at
    # "nothing_submitted" — so an inverted gate would pass for the wrong reason.
    monkeypatch.setattr(
        svc,
        "_read_submitted_lines_for_date",
        lambda _wd: [_submitted_line(1, "Ada", 10, abbr="TB3")],
    )
    monkeypatch.setattr(
        svc,
        "_claim_notification",
        lambda **kw: claim_calls.append(kw) or True,
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_calls.append(kw)
            return {"id": 1}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger())

    assert claim_calls == []
    assert enqueue_calls == []


def test_date_gate_releases_when_pending_zero_enqueues_outbox_row(monkeypatch):
    svc = _notification_svc_with_patches(monkeypatch)
    line = _submitted_line(1, "Ada", 10, abbr="TB3")
    enqueue_calls = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(
        svc, "_read_submitted_lines_for_date", lambda _wd: [line],
    )
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: True)
    monkeypatch.setattr(
        svc,
        "_fetch_recipients",
        lambda _cl_id: {10: {"pms": [], "owners": []}},
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_calls.append(kw)
            return {"id": 99}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger())

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["mode"] == "draft"


def test_date_gate_fail_closed_when_pending_lookup_returns_none(
    monkeypatch,
):
    svc = _notification_svc_with_patches(monkeypatch)
    claim_calls = []
    enqueue_calls = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: None)
    monkeypatch.setattr(
        svc,
        "_read_submitted_lines_for_date",
        lambda _wd: [_submitted_line(1, "Ada", 10)],
    )
    monkeypatch.setattr(
        svc,
        "_claim_notification",
        lambda **kw: claim_calls.append(kw) or True,
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_calls.append(kw)
            return {"id": 1}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger())

    assert claim_calls == []
    assert enqueue_calls == []


# === B. Consolidation shape ===


def test_one_draft_per_project_not_per_worker_two_projects_two_enqueues(
    monkeypatch,
):
    svc = _notification_svc_with_patches(monkeypatch)
    lines = [
        _submitted_line(1, "WorkerA", 10, abbr="P10"),
        _submitted_line(2, "WorkerB", 10, abbr="P10"),
        _submitted_line(3, "WorkerC", 20, abbr="P20"),
    ]
    enqueue_calls = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: lines)
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: True)
    monkeypatch.setattr(
        svc,
        "_fetch_recipients",
        lambda _cl_id: {
            10: {"pms": [], "owners": []},
            20: {"pms": [], "owners": []},
        },
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_calls.append(kw)
            return {"id": len(enqueue_calls)}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger())

    assert len(enqueue_calls) == 2
    subjects = {c["subject"] for c in enqueue_calls}
    assert "Contract Labor - P10 - 2026-07-15" in subjects
    assert "Contract Labor - P20 - 2026-07-15" in subjects


def test_outbound_subject_is_worker_agnostic_project_and_date_only(monkeypatch):
    svc = _notification_svc_with_patches(monkeypatch)
    lines = [
        _submitted_line(1, "Brayan", 10, abbr="TB3"),
        _submitted_line(2, "Elmer", 10, abbr="TB3"),
    ]
    captured_subject = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: lines)
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: True)
    monkeypatch.setattr(
        svc, "_fetch_recipients", lambda _cl_id: {10: {"pms": [], "owners": []}},
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            captured_subject.append(kw["subject"])
            return {"id": 1}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger(work_date="2026-07-15"))

    assert captured_subject == ["Contract Labor - TB3 - 2026-07-15"]
    subject = captured_subject[0]
    assert "Brayan" not in subject
    assert "Elmer" not in subject


def test_nothing_submitted_for_date_skips_claim_and_enqueue(monkeypatch):
    svc = _notification_svc_with_patches(monkeypatch)
    claim_calls = []
    enqueue_calls = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: [])
    monkeypatch.setattr(
        svc,
        "_claim_notification",
        lambda **kw: claim_calls.append(kw) or True,
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_calls.append(kw)
            return {"id": 1}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger())

    assert claim_calls == []
    assert enqueue_calls == []


# === C. The claim (dedup + F1 fix) ===


def test_claim_false_skips_project_without_enqueue(monkeypatch):
    svc = _notification_svc_with_patches(monkeypatch)
    line = _submitted_line(1, "Ada", 10, abbr="TB3")
    enqueue_calls = []
    fetch_recipient_calls = []
    released = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: [line])
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: False)

    def _fetch_recipients(cl_id):
        fetch_recipient_calls.append(cl_id)
        return {10: {"pms": [], "owners": []}}

    monkeypatch.setattr(svc, "_fetch_recipients", _fetch_recipients)
    monkeypatch.setattr(
        svc,
        "_release_notification_claim",
        lambda *, project_id, work_date: released.append((project_id, work_date)),
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_calls.append(kw)
            return {"id": 1}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger())

    assert fetch_recipient_calls == []
    assert enqueue_calls == []
    assert released == []


def test_enqueue_raises_releases_claim_for_that_project(monkeypatch):
    svc = _notification_svc_with_patches(monkeypatch)
    work_d = date(2026, 7, 15)
    line = _submitted_line(1, "Ada", 10, abbr="TB3")
    released = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: [line])
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: True)
    monkeypatch.setattr(
        svc, "_fetch_recipients", lambda _cl_id: {10: {"pms": [], "owners": []}},
    )
    monkeypatch.setattr(
        svc,
        "_release_notification_claim",
        lambda *, project_id, work_date: released.append((project_id, work_date)),
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            raise RuntimeError("ms down")

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger(work_date=work_d))

    assert released == [(10, work_d)]


def test_enqueue_falsy_row_releases_claim_and_does_not_count_as_enqueued(
    monkeypatch,
):
    svc = _notification_svc_with_patches(monkeypatch)
    work_d = date(2026, 7, 15)
    line = _submitted_line(1, "Ada", 10, abbr="TB3")
    released = []
    enqueue_attempts = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: [line])
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: True)
    monkeypatch.setattr(
        svc, "_fetch_recipients", lambda _cl_id: {10: {"pms": [], "owners": []}},
    )
    monkeypatch.setattr(
        svc,
        "_release_notification_claim",
        lambda *, project_id, work_date: released.append((project_id, work_date)),
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_attempts.append(kw)
            return None

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger(work_date=work_d))

    assert len(enqueue_attempts) == 1
    assert released == [(10, work_d)]


def test_fetch_recipients_failure_on_first_project_isolates_second_still_enqueues(
    monkeypatch,
):
    svc = _notification_svc_with_patches(monkeypatch)
    work_d = date(2026, 7, 15)
    lines = [
        _submitted_line(1, "A", 10, abbr="P10"),
        _submitted_line(2, "B", 20, abbr="P20"),
    ]
    enqueue_calls = []
    released = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: lines)
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: True)

    def _fetch(cl_id):
        if cl_id == 1:
            raise ValueError("recipients unavailable for project 10")
        return {20: {"pms": [], "owners": []}}

    monkeypatch.setattr(svc, "_fetch_recipients", _fetch)
    monkeypatch.setattr(
        svc,
        "_release_notification_claim",
        lambda *, project_id, work_date: released.append((project_id, work_date)),
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            enqueue_calls.append(kw)
            return {"id": len(enqueue_calls)}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService",
        _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger(work_date=work_d))

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["subject"] == "Contract Labor - P20 - 2026-07-15"
    assert released == [(10, work_d)]


# === D. Crew-wide apply ===


def _crew_apply_patches(monkeypatch, crew, *, single_cl_side_effect=None):
    svc = ContractLaborService(repo=MagicMock())
    project = types.SimpleNamespace(id=42, public_id="proj-uuid")
    single_calls = []

    def _single(**kwargs):
        single_calls.append(kwargs.get("contract_labor_public_id"))
        if single_cl_side_effect:
            return single_cl_side_effect(**kwargs)
        return {"review_status": "Approved"}

    monkeypatch.setattr(svc, "_apply_decision_to_single_cl", _single)
    monkeypatch.setattr(
        "entities.contract_labor.business.service.ProjectService",
        lambda: types.SimpleNamespace(
            read_by_public_id=lambda **kw: project,
        ),
    )
    svc.repo.read_reviewable_crew_by_project_and_date = MagicMock(return_value=crew)
    return svc, single_calls


def test_crew_apply_invokes_per_cl_primitive_once_per_member(monkeypatch):
    crew = [
        {"contract_labor_public_id": "cl-1", "employee_name": "A"},
        {"contract_labor_public_id": "cl-2", "employee_name": "B"},
        {"contract_labor_public_id": "cl-3", "employee_name": "C"},
    ]
    svc, single_calls = _crew_apply_patches(monkeypatch, crew)

    result = svc.apply_reviewer_decision(
        project_public_id="proj-uuid",
        work_date="2026-07-15",
        decision="approved",
        reviewer_email="pm@test.com",
        sub_cost_code_public_id="scc-uuid",
    )

    assert single_calls == ["cl-1", "cl-2", "cl-3"]
    assert result["applied_count"] == 3
    assert result["crew_size"] == 3


def test_crew_apply_partial_failure_returns_applied_count_and_failures_list(
    monkeypatch,
):
    crew = [
        {"contract_labor_public_id": "cl-1", "employee_name": "A"},
        {"contract_labor_public_id": "cl-2", "employee_name": "B"},
        {"contract_labor_public_id": "cl-3", "employee_name": "C"},
    ]

    def _single(**kwargs):
        pid = kwargs["contract_labor_public_id"]
        if pid == "cl-2":
            raise ValueError("authz denied")
        return {"review_status": "Approved"}

    svc, _ = _crew_apply_patches(monkeypatch, crew, single_cl_side_effect=_single)

    result = svc.apply_reviewer_decision(
        project_public_id="proj-uuid",
        work_date="2026-07-15",
        decision="approved",
        reviewer_email="pm@test.com",
        sub_cost_code_public_id="scc-uuid",
    )

    assert result["applied_count"] == 2
    assert len(result["failures"]) == 1
    assert "cl-2" in result["failures"][0]


def test_crew_apply_all_members_fail_raises_value_error(monkeypatch):
    crew = [
        {"contract_labor_public_id": "cl-1", "employee_name": "A"},
        {"contract_labor_public_id": "cl-2", "employee_name": "B"},
    ]

    def _single(**kwargs):
        raise ValueError("nope")

    svc, _ = _crew_apply_patches(monkeypatch, crew, single_cl_side_effect=_single)

    with pytest.raises(ValueError, match="failed for all"):
        svc.apply_reviewer_decision(
            project_public_id="proj-uuid",
            work_date="2026-07-15",
            decision="approved",
            reviewer_email="pm@test.com",
            sub_cost_code_public_id="scc-uuid",
        )


def test_crew_apply_empty_crew_raises_value_error(monkeypatch):
    svc, _ = _crew_apply_patches(monkeypatch, [])

    with pytest.raises(ValueError, match="No reviewable ContractLabor"):
        svc.apply_reviewer_decision(
            project_public_id="proj-uuid",
            work_date="2026-07-15",
            decision="approved",
            reviewer_email="pm@test.com",
            sub_cost_code_public_id="scc-uuid",
        )


# === E. F4 REGRESSION — conditional ready-flip ===


def _line_item(
    *,
    li_id,
    project_id,
    sub_cost_code_id,
    row_version=b"\x01",
):
    return types.SimpleNamespace(
        id=li_id,
        project_id=project_id,
        sub_cost_code_id=sub_cost_code_id,
        row_version_bytes=row_version,
        line_date="2026-07-15",
        description="d",
        hours=8,
        rate=50,
        markup=0.1,
        price=440,
        is_billable=True,
        is_overhead=False,
        bill_line_item_id=None,
    )


def _apply_single_cl_patches(monkeypatch, svc, *, all_line_items, target_project_id=100):
    cl = types.SimpleNamespace(id=10, public_id="cl-1", status="submitted")
    project = types.SimpleNamespace(id=target_project_id, public_id="proj-a")
    scc = types.SimpleNamespace(id=555, public_id="scc-uuid")
    approved_status = types.SimpleNamespace(
        id=1, name="Approved", is_final=True, is_declined=False,
    )
    new_review = types.SimpleNamespace(
        id=99,
        review_status_id=1,
        status_is_final=True,
        status_is_declined=False,
    )
    recipient = types.SimpleNamespace(
        ProjectId=target_project_id,
        UserId=7,
        Email="pm@test.com",
        Firstname="Pat",
        Lastname="M",
    )

    monkeypatch.setattr(svc, "read_by_public_id", lambda **kw: cl)
    monkeypatch.setattr(
        "entities.contract_labor.business.service.ProjectService",
        lambda: types.SimpleNamespace(read_by_public_id=lambda **kw: project),
    )

    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [recipient]
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda *a: None
    monkeypatch.setattr(
        "shared.database.get_connection",
        lambda: conn,
    )
    monkeypatch.setattr(
        "shared.database.call_procedure",
        lambda **kw: None,
    )

    monkeypatch.setattr(
        "entities.sub_cost_code.business.service.SubCostCodeService",
        lambda: types.SimpleNamespace(read_by_public_id=lambda **kw: scc),
    )

    li_repo = MagicMock()
    li_repo.read_by_contract_labor_id.return_value = all_line_items
    monkeypatch.setattr(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository",
        lambda: li_repo,
    )

    monkeypatch.setattr(
        "entities.review_status.business.service.ReviewStatusService",
        lambda: types.SimpleNamespace(
            read_all=lambda: [approved_status],
            read_by_id=lambda id: approved_status,
        ),
    )
    monkeypatch.setattr(
        "entities.review.persistence.repo.ReviewRepository",
        lambda: types.SimpleNamespace(create=lambda **kw: new_review),
    )

    ready_calls = []
    monkeypatch.setattr(
        svc,
        "mark_as_ready_via_review_approval",
        lambda **kw: ready_calls.append(kw) or cl,
    )
    return ready_calls


def test_apply_delegates_ready_flip_to_choke_point(monkeypatch):
    """The call site must NOT re-implement the coded-check — it delegates.

    Both ready-flip callers (ReviewService.create and this one) funnel through
    mark_as_ready_via_review_approval, which owns the invariant. A duplicate
    call-site check would drift; this pins the delegation.
    """
    svc = ContractLaborService(repo=MagicMock())
    lines = [_line_item(li_id=1, project_id=100, sub_cost_code_id=5)]
    ready_calls = _apply_single_cl_patches(
        monkeypatch, svc, all_line_items=lines, target_project_id=100,
    )

    svc._apply_decision_to_single_cl(
        contract_labor_public_id="cl-1",
        project_public_id="proj-a",
        decision="approved",
        reviewer_email="pm@test.com",
        sub_cost_code_public_id="scc-uuid",
    )

    assert ready_calls == [{"contract_labor_id": 10}]


# --- the invariant itself, tested on the choke point (all callers) ---


def _choke_point_svc(monkeypatch, *, lines):
    """ContractLaborService whose repo + line-item repo are stubbed, so
    mark_as_ready_via_review_approval can be exercised directly."""
    repo = MagicMock()
    existing = types.SimpleNamespace(id=10, public_id="cl-1", status="submitted")
    repo.read_by_id.return_value = existing
    svc = ContractLaborService(repo=repo)

    li_repo = MagicMock()
    li_repo.read_by_contract_labor_id.return_value = lines
    monkeypatch.setattr(
        "entities.contract_labor.persistence.line_item_repo."
        "ContractLaborLineItemRepository",
        lambda: li_repo,
    )
    return svc, repo, existing


def test_choke_point_defers_ready_when_another_projects_lines_uncoded(monkeypatch):
    svc, repo, existing = _choke_point_svc(
        monkeypatch,
        lines=[
            _line_item(li_id=1, project_id=100, sub_cost_code_id=5),
            _line_item(li_id=2, project_id=200, sub_cost_code_id=None),
        ],
    )

    result = svc.mark_as_ready_via_review_approval(contract_labor_id=10)

    repo.update_by_id.assert_not_called()
    assert existing.status == "submitted"
    assert result is existing


def test_choke_point_flips_ready_when_all_project_lines_coded(monkeypatch):
    svc, repo, existing = _choke_point_svc(
        monkeypatch,
        lines=[
            _line_item(li_id=1, project_id=100, sub_cost_code_id=5),
            _line_item(li_id=2, project_id=200, sub_cost_code_id=6),
        ],
    )

    svc.mark_as_ready_via_review_approval(contract_labor_id=10)

    repo.update_by_id.assert_called_once()
    assert existing.status == "ready"


def test_choke_point_overhead_line_without_scc_does_not_block_ready(monkeypatch):
    svc, repo, existing = _choke_point_svc(
        monkeypatch,
        lines=[
            _line_item(li_id=1, project_id=100, sub_cost_code_id=5),
            _line_item(li_id=2, project_id=None, sub_cost_code_id=None),
        ],
    )

    svc.mark_as_ready_via_review_approval(contract_labor_id=10)

    repo.update_by_id.assert_called_once()
    assert existing.status == "ready"


# === F. Structural pins over SQL file ===


_CONTRACT_LABOR_SQL = (
    Path(__file__).resolve().parents[1]
    / "entities/contract_labor/sql/dbo.contract_labor.sql"
)


def test_reply_crew_sproc_matches_draft_body_submitted_status_not_pending_review():
    draft_body = _sproc_body(
        _CONTRACT_LABOR_SQL, "ReadSubmittedContractLaborLinesByWorkDate",
    )
    reply_crew = _sproc_body(
        _CONTRACT_LABOR_SQL, "ReadReviewableContractLaborByProjectAndDate",
    )

    draft_status = re.search(
        r"cl\.\[Status\]\s*=\s*'submitted'", draft_body, flags=re.IGNORECASE,
    )
    reply_status = re.search(
        r"cl\.\[Status\]\s*=\s*'submitted'", reply_crew, flags=re.IGNORECASE,
    )
    assert draft_status and reply_status

    assert "pending_review" not in reply_crew.lower()


def test_sql_file_has_no_workdate_backfill_update_on_contract_labor_notification():
    sql_text = _CONTRACT_LABOR_SQL.read_text(encoding="utf-8")
    executable = strip_comments_and_strings(sql_text)
    # Two spellings must both be caught. The retired backfill used the ALIAS
    # form (`UPDATE cln SET ... FROM dbo.[ContractLaborNotification] cln`), so a
    # pin that only matches `UPDATE ContractLaborNotification SET` would have
    # missed the very defect it exists to prevent (proven by mutation).
    direct = re.search(
        r"UPDATE\s+(?:dbo\.)?\[?ContractLaborNotification\]?[^;]*?\bSET\b[^;]*?\bWorkDate\b",
        executable,
        flags=re.IGNORECASE | re.DOTALL,
    )
    aliased = re.search(
        r"UPDATE\s+\w+[^;]*?\bSET\b[^;]*?\bWorkDate\b[^;]*?\bFROM\b[^;]*?"
        r"\[?ContractLaborNotification\]?",
        executable,
        flags=re.IGNORECASE | re.DOTALL,
    )
    backfill = direct or aliased
    assert backfill is None, (
        "Executable SQL must not UPDATE ContractLaborNotification.WorkDate "
        "(legacy NULL WorkDate rows must not become consolidated claims)."
    )
