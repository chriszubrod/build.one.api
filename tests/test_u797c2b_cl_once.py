"""U-797c2b item 2: the Contract Labor decision resolves the v2 record ONCE per decision.

Pure fakes only. No database, Graph or HTTP. Every outbound call is replaced.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from entities.contract_labor.business import service as cl_service_mod
from shared.authz import provenance as prov
from shared.authz.provenance import ReviewerProvenanceRefused

CREW = [
    {"contract_labor_id": 7, "contract_labor_public_id": "cl-a", "employee_name": "A"},
    {"contract_labor_id": 8, "contract_labor_public_id": "cl-b", "employee_name": "B"},
]


def _n(nid, *, project_id=1, work_date="2026-11-19", subject=None, kind="contract_labor"):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date, Subject=subject)


@pytest.fixture
def flag(monkeypatch):
    def set_flag(on: bool):
        monkeypatch.setattr(prov, "_binder_v2_enabled", lambda: on)
    return set_flag


@pytest.fixture
def env(monkeypatch):
    """A ContractLaborService with every outbound step replaced; records what happened."""
    rec = SimpleNamespace(generic=0, lookups=0, legacy=[], applied=[], table={})
    em = SimpleNamespace(id=1, public_id="EM1", conversation_id="Conv-A", folder="inbox",
                         from_address="pm@x.test", subject="Review: CL - Proj - 2026-11-19")

    def fake_generic(**kw):
        rec.generic += 1
        return em

    def fake_lookup(self, conversation_id):
        rec.lookups += 1
        return list(rec.table.get(conversation_id, []))

    def fake_legacy(**kw):
        rec.legacy.append(kw["expected_conversation_id"])
        return "LEGACY_EM"

    monkeypatch.setattr(prov, "bind_reviewer_reply", fake_generic)
    monkeypatch.setattr(prov, "bind_reviewer_reply_to_thread", fake_legacy)
    from entities.review.business import review_notification_service as rns
    monkeypatch.setattr(rns.ReviewNotificationRecordService, "lookup_for_reply", fake_lookup)
    monkeypatch.setattr(cl_service_mod.ProjectService, "read_by_public_id",
                        lambda self, public_id: SimpleNamespace(id=1))

    svc = cl_service_mod.ContractLaborService.__new__(cl_service_mod.ContractLaborService)
    svc.repo = SimpleNamespace(read_reviewable_crew_by_project_and_date=lambda **kw: list(CREW))
    monkeypatch.setattr(svc, "_expected_cl_reviewer_reply_conversation_id",
                        lambda **kw: "Conv-A")
    monkeypatch.setattr(svc, "_apply_decision_to_single_cl",
                        lambda **kw: rec.applied.append(kw["contract_labor_public_id"]) or {"review_status": "x"})
    rec.svc = svc
    rec.em = em
    return rec


def _apply(rec):
    return rec.svc.apply_reviewer_decision(
        project_public_id="pp", work_date="2026-11-19", decision="rejected",
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1")


def _apply_ok(rec):
    try:
        return _apply(rec)
    except ReviewerProvenanceRefused:
        raise
    except Exception:
        return None  # the post-loop review bookkeeping is not under test here


# ---------------------------------------------------------------------------
# resolve_reply_once: flag OFF touches nothing; flag ON resolves once
# ---------------------------------------------------------------------------
def test_resolve_once_flag_off_is_none_with_no_generic_check_and_no_lookup(monkeypatch, flag, env):
    flag(False)
    got = prov.resolve_reply_once(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=("contract_labor", 7), what="w", project_id=1, work_date="2026-11-19")
    assert got is None
    assert env.generic == 0
    assert env.lookups == 0


def test_resolve_once_flag_on_bound_returns_the_reply(flag, env):
    flag(True)
    env.table["Conv-A"] = [(_n(1), {("contract_labor", 7)})]
    got = prov.resolve_reply_once(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=("contract_labor", 7), what="w", project_id=1, work_date="2026-11-19")
    assert got is env.em
    assert env.lookups == 1


def test_resolve_once_flag_on_no_row_returns_none(flag, env):
    flag(True)
    got = prov.resolve_reply_once(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=("contract_labor", 7), what="w", project_id=1, work_date="2026-11-19")
    assert got is None
    assert env.lookups == 1


def test_resolve_once_flag_on_refusal_raises(flag, env):
    flag(True)
    env.table["Conv-A"] = [(_n(1, project_id=2), {("contract_labor", 7)})]
    with pytest.raises(ReviewerProvenanceRefused):
        prov.resolve_reply_once(
            reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
            target=("contract_labor", 7), what="w", project_id=1, work_date="2026-11-19")


# ---------------------------------------------------------------------------
# apply_reviewer_decision: one lookup per decision, same outcomes as today
# ---------------------------------------------------------------------------
def test_flag_on_bound_decision_looks_up_once_for_the_whole_crew(flag, env):
    flag(True)
    env.table["Conv-A"] = [(_n(1), {("contract_labor", 7), ("contract_labor", 8)})]
    _apply_ok(env)
    assert env.lookups == 1, "one record lookup per decision, not per crew member"
    assert env.legacy == [], "a bound record never runs the legacy binding"
    assert env.applied == ["cl-a", "cl-b"]


def test_flag_on_no_row_keeps_per_member_legacy_binding(flag, env):
    flag(True)
    _apply_ok(env)
    assert env.lookups == 1
    assert env.legacy == ["Conv-A", "Conv-A"], "today's per-member legacy binding, unchanged"
    assert env.applied == ["cl-a", "cl-b"]


def test_flag_on_refusal_stops_before_any_crew_member_is_written(flag, env):
    flag(True)
    env.table["Conv-A"] = [(_n(1, project_id=2), {("contract_labor", 7)})]
    with pytest.raises(ReviewerProvenanceRefused):
        _apply(env)
    assert env.applied == [], "refusal happens before any write"
    assert env.legacy == [], "no legacy fallback after a refusal"


def test_flag_off_is_per_member_legacy_exactly_as_today(flag, env):
    flag(False)
    _apply_ok(env)
    assert env.lookups == 0
    assert env.generic == 0
    assert env.legacy == ["Conv-A", "Conv-A"]
    assert env.applied == ["cl-a", "cl-b"]


def test_flag_on_subject_mismatch_falls_back_to_legacy_per_member(flag, env):
    flag(True)
    env.table["Conv-A"] = [(_n(1, subject="Some retitled draft"), {("contract_labor", 7)})]
    _apply_ok(env)
    assert env.legacy == ["Conv-A", "Conv-A"]
    assert env.applied == ["cl-a", "cl-b"]


# ---------------------------------------------------------------------------
# single-member path: unchanged; the helper without v2_status still does its own check
# ---------------------------------------------------------------------------
def test_single_member_helper_without_v2_status_still_checks_itself(flag, env):
    flag(True)
    env.table["Conv-A"] = [(_n(1), {("contract_labor", 7)})]
    got = env.svc._bind_cl_decision_reply(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        contract_labor_id=7, contract_labor_public_id="cp", project_public_id="pp", project_id=1,
        work_date="2026-11-19")
    assert got is env.em
    assert env.lookups == 1
    assert env.legacy == []


def test_single_member_helper_refuses_mismatched_project_and_date_as_before(flag, env):
    flag(True)
    env.table["Conv-A"] = [(_n(1, work_date="2026-11-18"), {("contract_labor", 7)})]
    with pytest.raises(ReviewerProvenanceRefused):
        env.svc._bind_cl_decision_reply(
            reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
            contract_labor_id=7, contract_labor_public_id="cp", project_public_id="pp", project_id=1,
            work_date="2026-11-19")
    assert env.legacy == []


def test_apply_decision_crew_loop_source_uses_the_once_resolver():
    import inspect
    src = inspect.getsource(cl_service_mod.ContractLaborService.apply_reviewer_decision)
    assert "resolve_reply_once" in src
    assert "legacy_only" in src
