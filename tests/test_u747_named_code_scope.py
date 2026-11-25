"""U-747 round 2: contract_labor_public_ids narrows an apply to the workers the replied-to email listed.

Binding is unchanged: a reply binds exactly as today, and the list never relaxes provenance. Without
a list, named-code mode keeps today's whole-live-crew behaviour (stragglers included). Crew members
the list does not name are untouched and not reported. A listed id that is not reviewable on the
project and date is reported as not_applied and never written, and its record is not read.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from entities.contract_labor.api import router as cl_router
from entities.contract_labor.business import service as svc
from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.intelligence import tools as cl_tools
from entities.review.business import cl_notification_service as cns_module
from entities.review.business.review_notification_service import (
    CrewTargetSnapshot,
    ReviewNotificationRecordService,
)
from shared.authz import provenance as prov

_REAL_RESOLVE_REPLY_ONCE = prov.resolve_reply_once
_REAL_RESOLVE_NOTIFICATION_ONCE = prov.resolve_reply_notification_once

NOT_REVIEWABLE = "not reviewable on this project and date"
EM = SimpleNamespace(id=7, conversation_id="Conv-B", subject="Crew", public_id="EM7")
NOTIF = SimpleNamespace(Id=99)

ANN, BEN, CAL, DEE, WESTON, LATE = 1, 2, 3, 4, 5, 6


def _pub(n):
    return f"00000000-0000-0000-0000-{n:012d}"


def _hexpub(n):
    """A public id with hex letters, so upper/lower case really differ."""
    return f"abcdefab-cdef-abcd-efab-{n:012d}"


def _member(n, name):
    return {"contract_labor_id": n, "contract_labor_public_id": _pub(n), "employee_name": name}


def _drywall_and_weston():
    return [_member(ANN, "Ann"), _member(BEN, "Ben"), _member(CAL, "Cal"), _member(DEE, "Dee"),
            _member(WESTON, "Weston")]


def _snap(n):
    return CrewTargetSnapshot(
        contract_labor_id=n, suggested_sub_cost_code_id=77, suggestion_id=5,
        suggestion_source="history", suggestion_may_split=False, lines_fingerprint="fp-ok",
    )


class FakeProject:
    def read_by_public_id(self, public_id):
        return SimpleNamespace(id=9, public_id=public_id)


class FakeRepo:
    def __init__(self, st):
        self.st = st

    def read_reviewable_crew_by_project_and_date(self, *, project_id, work_date):
        return list(self.st.crew)

    def update_aggregates(self, *, id, conn=None):
        pass


@pytest.fixture
def st_orch(monkeypatch):
    st = SimpleNamespace(
        crew=[], bound=True, notif_bound=True, suggestion_flag=False, snaps={},
        apply_calls=[], bind_cl_calls=[], once_calls=[], notif_calls=[], snapshot_reads=[], behave={},
        record_exists=False, exists_error=None, exists_calls=[], binder_flag=True,
    )
    monkeypatch.setattr(cns_module, "cl_suggestions_in_review_enabled", lambda settings=None: st.suggestion_flag)
    monkeypatch.setattr(svc, "ProjectService", FakeProject)

    def fake_once(**kw):
        st.once_calls.append(kw)
        return EM if st.bound else None

    def fake_notif_once(**kw):
        st.notif_calls.append(kw)
        return (EM, NOTIF) if st.notif_bound else None

    monkeypatch.setattr(prov, "resolve_reply_once", fake_once)
    monkeypatch.setattr(prov, "resolve_reply_notification_once", fake_notif_once)
    monkeypatch.setattr(prov, "_binder_v2_enabled", lambda: st.binder_flag)

    def fake_record_exists(self, *, project_id, work_date):
        st.exists_calls.append((project_id, work_date))
        if st.exists_error is not None:
            raise st.exists_error
        return st.record_exists

    monkeypatch.setattr(ReviewNotificationRecordService, "contract_labor_record_exists", fake_record_exists)

    def fake_read_snapshots(self, notification_id):
        st.snapshot_reads.append(notification_id)
        return dict(st.snaps)

    monkeypatch.setattr(ReviewNotificationRecordService, "read_crew_snapshots", fake_read_snapshots)

    def fake_apply(self, **kw):
        st.apply_calls.append(kw)
        behave = st.behave.get(kw["contract_labor_public_id"])
        if isinstance(behave, BaseException):
            raise behave
        return {"decision_applied": kw["decision"], "review_status": "Approved",
                "contract_labor_public_id": kw["contract_labor_public_id"], "ready_flipped": False}

    monkeypatch.setattr(ContractLaborService, "_apply_decision_to_single_cl", fake_apply)

    def fake_bind_cl(self, **kw):
        st.bind_cl_calls.append(kw)
        return SimpleNamespace(id=500)

    monkeypatch.setattr(ContractLaborService, "_bind_cl_decision_reply", fake_bind_cl)
    return ContractLaborService(repo=FakeRepo(st)), st


def _call(service, *, decision="approved", sub="scc-named", listed=None, names=None):
    kwargs = dict(
        project_public_id="proj-1", work_date="2026-10-07", decision=decision,
        reviewer_email="pm@x.test", sub_cost_code_public_id=sub, reviewer_email_message_public_id="EM7",
    )
    if listed is not None:
        kwargs["contract_labor_public_ids"] = listed
    if names is not None:
        kwargs["contract_labor_worker_names"] = names
    return service.apply_reviewer_decision(**kwargs)


def _applied(st):
    return sorted(c["contract_labor_public_id"] for c in st.apply_calls)


# --- named-code mode: a list narrows the crew, binding unchanged -----------------------------------

def test_hp2_1006_recorded_thread_list_of_drywall_leaves_weston_untouched(st_orch):
    service, st = st_orch
    st.crew = _drywall_and_weston()
    result = _call(service, listed=[_pub(ANN), _pub(BEN), _pub(CAL), _pub(DEE)])
    assert result["applied_count"] == 4
    assert _applied(st) == sorted(_pub(n) for n in (ANN, BEN, CAL, DEE))
    assert all(_pub(WESTON) not in str(e) for e in result["not_applied"])
    assert st.bind_cl_calls == []  # the reply bound to the record: no per-member legacy binding


def test_hp2_1007_unbound_reply_with_list_of_weston_codes_only_weston(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = _drywall_and_weston()
    result = _call(service, listed=[_pub(WESTON)])
    assert result["applied_count"] == 1
    assert _applied(st) == [_pub(WESTON)]
    assert [c["contract_labor_id"] for c in st.bind_cl_calls] == [WESTON]
    assert all(c["legacy_only"] is True for c in st.bind_cl_calls)


def test_hp2_1007_unbound_reply_no_list_refused_when_a_crew_record_exists(st_orch):
    service, st = st_orch
    st.bound = False
    st.record_exists = True
    st.crew = _drywall_and_weston()
    with pytest.raises(ValueError, match="not on the recorded crew review email"):
        _call(service)
    assert st.apply_calls == [] and st.bind_cl_calls == []
    assert st.exists_calls == [(9, "2026-10-07")]


def test_record_exists_but_list_given_applies_the_listed_workers_only(st_orch):
    service, st = st_orch
    st.bound = False
    st.record_exists = True
    st.crew = _drywall_and_weston()
    result = _call(service, listed=[_pub(WESTON)])
    assert _applied(st) == [_pub(WESTON)]
    assert result["applied_count"] == 1
    assert st.exists_calls == []  # the list path never consults the record


def test_existence_read_that_raises_refuses_fail_closed(st_orch, caplog):
    service, st = st_orch
    st.bound = False
    st.exists_error = RuntimeError("db down")
    st.crew = [_member(ANN, "Ann")]
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        with pytest.raises(ValueError, match="not on the recorded crew review email"):
            _call(service)
    assert st.apply_calls == [] and st.bind_cl_calls == []
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in logged and "db down" not in logged


def test_binder_flag_off_keeps_today_behaviour_and_never_checks_the_record(st_orch, caplog):
    service, st = st_orch
    st.binder_flag = False
    st.bound = False
    st.record_exists = True
    st.crew = [_member(ANN, "Ann"), _member(BEN, "Ben")]
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        result = _call(service)
    assert result["applied_count"] == 2
    assert st.exists_calls == []
    assert not [r for r in caplog.records if "legacy_whole_crew_no_record" in r.getMessage()]


def test_recorded_thread_without_list_codes_whole_live_crew_including_a_late_added_worker(st_orch):
    service, st = st_orch
    st.crew = [_member(ANN, "Ann"), _member(BEN, "Ben"), _member(LATE, "Late")]
    result = _call(service)
    assert result["applied_count"] == 3
    assert _applied(st) == sorted(_pub(n) for n in (ANN, BEN, LATE))
    assert result["not_applied"] == []


def test_no_record_and_no_list_codes_whole_crew_with_legacy_warning(st_orch, caplog):
    service, st = st_orch
    st.bound = False
    st.crew = [_member(ANN, "Ann"), _member(BEN, "Ben")]
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        result = _call(service)
    assert result["applied_count"] == 2
    assert len(st.bind_cl_calls) == 2
    assert st.exists_calls == [(9, "2026-10-07")]
    warnings = [r.getMessage() for r in caplog.records if "cl_decision.legacy_whole_crew_no_record" in r.getMessage()]
    assert warnings and "crew=2" in warnings[0]


def test_listed_id_not_reviewable_is_reported_and_never_written(st_orch):
    service, st = st_orch
    st.crew = [_member(ANN, "Ann"), _member(BEN, "Ben")]
    result = _call(service, listed=[_pub(ANN), _pub(99)])
    assert _applied(st) == [_pub(ANN)]
    assert result["not_applied"] == [
        {"contract_labor_public_id": _pub(99), "employee_name": None, "reason": NOT_REVIEWABLE},
    ]


def test_list_with_no_crew_member_refuses_before_any_write(st_orch):
    service, st = st_orch
    st.crew = [_member(ANN, "Ann")]
    with pytest.raises(ValueError, match="not reviewable on this project and date"):
        _call(service, listed=[_pub(99)])
    assert st.apply_calls == [] and st.bind_cl_calls == []


def test_duplicate_listed_ids_are_applied_once(st_orch):
    service, st = st_orch
    st.crew = [_member(ANN, "Ann"), _member(BEN, "Ben")]
    result = _call(service, listed=[_pub(ANN), _pub(ANN).upper()])
    assert _applied(st) == [_pub(ANN)]
    assert result["not_applied"] == []


def test_malformed_listed_id_refused_before_any_write(st_orch):
    service, st = st_orch
    st.crew = [_member(ANN, "Ann")]
    with pytest.raises(ValueError, match="malformed"):
        _call(service, listed=["not-a-uuid"])
    assert st.apply_calls == [] and st.bind_cl_calls == [] and st.once_calls == []


# --- suggestion mode: the list narrows before snapshot eligibility ----------------------------------

def test_suggestion_mode_list_narrows_to_the_listed_member(st_orch):
    service, st = st_orch
    st.suggestion_flag = True
    st.crew = [_member(ANN, "Ann"), _member(BEN, "Ben")]
    st.snaps = {ANN: _snap(ANN), BEN: _snap(BEN)}
    result = _call(service, sub=None, listed=[_pub(ANN)])
    assert result["mode"] == "suggested"
    assert _applied(st) == [_pub(ANN)]
    assert result["not_applied"] == []  # Ben is untouched and not reported


def test_suggestion_mode_without_list_is_unchanged(st_orch):
    service, st = st_orch
    st.suggestion_flag = True
    st.crew = [_member(ANN, "Ann"), _member(BEN, "Ben")]
    st.snaps = {ANN: _snap(ANN), BEN: _snap(BEN)}
    result = _call(service, sub=None)
    assert result["mode"] == "suggested"
    assert result["applied_count"] == 2
    assert result["not_applied"] == []
    assert st.snapshot_reads == [99]


# --- router and agent tool surface -----------------------------------------------------------------

def test_router_passes_the_list_through_to_the_service(monkeypatch):
    seen = {}

    def fake_apply(self, **kw):
        seen.update(kw)
        return {"applied_count": 0}

    monkeypatch.setattr(ContractLaborService, "apply_reviewer_decision", fake_apply)
    body = {"project_public_id": "p", "work_date": "2026-10-07", "decision": "approved",
            "reviewer_email": "pm@x.test", "sub_cost_code_public_id": "s",
            "reviewer_email_message_public_id": "EM7", "contract_labor_public_ids": [_pub(ANN)]}
    cl_router.apply_reviewer_decision_router(body, current_user={})
    assert seen["contract_labor_public_ids"] == [_pub(ANN)]


def test_router_maps_a_malformed_listed_id_to_400(monkeypatch):
    body = {"project_public_id": "p", "work_date": "2026-10-07", "decision": "approved",
            "reviewer_email": "pm@x.test", "sub_cost_code_public_id": "s",
            "reviewer_email_message_public_id": "EM7", "contract_labor_public_ids": ["not-a-uuid"]}
    with pytest.raises(HTTPException) as exc:
        cl_router.apply_reviewer_decision_router(body, current_user={})
    assert exc.value.status_code == 400
    assert "malformed" in exc.value.detail


def test_tool_schema_declares_the_field_and_the_handler_forwards_it():
    assert "contract_labor_public_ids" in cl_tools.apply_contract_labor_reviewer_decision.input_schema["properties"]
    sent = {}

    async def call_api(method, path, body=None):
        sent.update(body)
        return {}

    args = {"project_public_id": "p", "work_date": "2026-10-07", "decision": "approved",
            "reviewer_email": "pm@x.test", "reviewer_email_message_public_id": "EM7",
            "contract_labor_public_ids": [_pub(ANN)]}
    asyncio.run(cl_tools._apply_contract_labor_reviewer_decision(args, SimpleNamespace(call_api=call_api)))
    assert sent["contract_labor_public_ids"] == [_pub(ANN)]


def test_recorded_thread_never_checks_the_record(st_orch):
    service, st = st_orch
    st.record_exists = True
    st.crew = _drywall_and_weston()
    result = _call(service)
    assert result["applied_count"] == 5
    assert st.exists_calls == []


# --- F1: worker names as a second selector --------------------------------------------------------

def _named(n, name):
    return {"contract_labor_id": n, "contract_labor_public_id": _pub(n), "employee_name": name}


def test_name_exact_match_selects_the_worker(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = [_named(ANN, "Ann"), _named(BEN, "Ben")]
    result = _call(service, names=["Ben"])
    assert _applied(st) == [_pub(BEN)]
    assert result["not_applied"] == []


def test_name_match_ignores_case_and_whitespace(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = [_named(ANN, "Ann Lee"), _named(BEN, "Ben")]
    _call(service, names=["  ann   LEE "])
    assert _applied(st) == [_pub(ANN)]


def test_ambiguous_duplicate_name_is_not_applied_and_refuses_when_nothing_else_selected(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = [_named(ANN, "Ann"), _named(4, "Dee"), _named(6, "Dee")]
    with pytest.raises(ValueError, match="ambiguous name — name the worker by id"):
        _call(service, names=["dee"])
    assert st.apply_calls == [] and st.bind_cl_calls == []


def test_ambiguous_name_next_to_an_id_is_reported_and_not_written(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = [_named(ANN, "Ann"), _named(4, "Dee"), _named(6, "Dee")]
    result = _call(service, listed=[_pub(ANN)], names=["Dee"])
    assert _applied(st) == [_pub(ANN)]
    assert {"contract_labor_public_id": None, "employee_name": "Dee",
            "reason": "ambiguous name — name the worker by id"} in result["not_applied"]


def test_unknown_name_is_reported_as_not_reviewable_and_the_id_still_applies(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = [_named(ANN, "Ann")]
    result = _call(service, listed=[_pub(ANN)], names=["Nobody"])
    assert _applied(st) == [_pub(ANN)]
    assert {"contract_labor_public_id": None, "employee_name": "Nobody",
            "reason": NOT_REVIEWABLE} in result["not_applied"]


def test_names_and_ids_are_a_deduplicated_union(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = [_named(ANN, "Ann"), _named(BEN, "Ben"), _named(CAL, "Cal")]
    _call(service, listed=[_pub(ANN)], names=["Ben", "ann"])
    assert _applied(st) == [_pub(ANN), _pub(BEN)]


@pytest.mark.parametrize("bad", ["Ann", [""], ["   "], ["x" * 201], [123]])
def test_malformed_worker_names_refuse_before_any_read_or_write(st_orch, bad):
    service, st = st_orch
    st.bound = False
    st.crew = [_named(ANN, "Ann")]
    with pytest.raises(ValueError):
        _call(service, names=bad)
    assert st.apply_calls == [] and st.bind_cl_calls == [] and st.once_calls == []


def test_name_list_skips_the_record_check_like_the_id_list(st_orch):
    service, st = st_orch
    st.bound = False
    st.record_exists = True
    st.crew = [_named(ANN, "Ann"), _named(BEN, "Ben")]
    _call(service, names=["Ben"])
    assert _applied(st) == [_pub(BEN)]
    assert st.exists_calls == []


# --- F4: uppercase crew ids (pyodbc) against lowercase lists, and the reverse ------------------------

def test_uppercase_crew_ids_select_against_a_lowercase_list(st_orch):
    service, st = st_orch
    st.bound = False
    # Hex letters, so .upper() really changes the id (pyodbc returns uniqueidentifier uppercase).
    ann_id, ben_id = _hexpub(ANN), _hexpub(BEN)
    assert ben_id.upper() != ben_id
    st.crew = [{"contract_labor_id": ANN, "contract_labor_public_id": ann_id.upper(), "employee_name": "Ann"},
               {"contract_labor_id": BEN, "contract_labor_public_id": ben_id.upper(), "employee_name": "Ben"}]
    result = _call(service, listed=[ben_id])
    assert [c["contract_labor_public_id"] for c in st.apply_calls] == [ben_id.upper()]
    assert result["not_applied"] == []


def test_lowercase_crew_ids_select_against_an_uppercase_list(st_orch):
    service, st = st_orch
    st.bound = False
    ann_id, ben_id = _hexpub(ANN), _hexpub(BEN)
    assert ann_id.upper() != ann_id
    st.crew = [{"contract_labor_id": ANN, "contract_labor_public_id": ann_id, "employee_name": "Ann"},
               {"contract_labor_id": BEN, "contract_labor_public_id": ben_id, "employee_name": "Ben"}]
    _call(service, listed=[ann_id.upper()])
    assert [c["contract_labor_public_id"] for c in st.apply_calls] == [ann_id]


# --- F2 and F5: why the refusal fired, and the paths that must not read the record ------------

def test_existence_refusal_logs_the_reason_with_ids_only(st_orch, caplog):
    service, st = st_orch
    st.bound = False
    st.record_exists = True
    st.crew = [_named(ANN, "Ann")]
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        with pytest.raises(ValueError):
            _call(service)
    msgs = [r.getMessage() for r in caplog.records if "cl_decision.unbound_reply_refused" in r.getMessage()]
    assert msgs == ["cl_decision.unbound_reply_refused project=proj-1 date=2026-10-07"]


def test_binder_on_unbound_with_a_list_and_no_record_reads_nothing_and_warns_nothing(st_orch, caplog):
    service, st = st_orch
    st.bound = False
    st.record_exists = False
    st.crew = [_named(ANN, "Ann"), _named(BEN, "Ben")]
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        _call(service, listed=[_pub(BEN)])
    assert st.exists_calls == []
    assert not [r for r in caplog.records if "legacy_whole_crew" in r.getMessage()]


def test_subject_mismatch_shaped_unbound_reply_with_a_record_is_refused(st_orch, monkeypatch):
    # The real resolver chain: the binder's subject-mismatch rescue returns no notification, so the
    # reply is unbound. The record for the day still exists, so the apply refuses before any write.
    service, st = st_orch
    st.crew = [_named(ANN, "Ann")]
    st.record_exists = True
    monkeypatch.setattr(prov, "resolve_reply_once", _REAL_RESOLVE_REPLY_ONCE)
    monkeypatch.setattr(prov, "resolve_reply_notification_once", _REAL_RESOLVE_NOTIFICATION_ONCE)
    monkeypatch.setattr(prov, "bind_reviewer_reply",
                        lambda **kw: SimpleNamespace(id=7, conversation_id="Conv-X", subject="Other"))
    monkeypatch.setattr(prov, "bind_reply_via_notification", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="not on the recorded crew review email"):
        _call(service)
    assert st.apply_calls == [] and st.bind_cl_calls == []
    assert st.exists_calls == [(9, "2026-10-07")]


# --- F1: the agent surface (tool schema, handler, prompt) --------------------------------------

def test_tool_schema_declares_worker_names_and_the_handler_forwards_them():
    assert "contract_labor_worker_names" in cl_tools.apply_contract_labor_reviewer_decision.input_schema["properties"]
    sent = {}

    async def call_api(method, path, body=None):
        sent.update(body)
        return {}

    args = {"project_public_id": "p", "work_date": "2026-10-07", "decision": "approved",
            "reviewer_email": "pm@x.test", "reviewer_email_message_public_id": "EM7",
            "contract_labor_worker_names": ["Ann Lee"]}
    asyncio.run(cl_tools._apply_contract_labor_reviewer_decision(args, SimpleNamespace(call_api=call_api)))
    assert sent["contract_labor_worker_names"] == ["Ann Lee"]


def test_agent_surface_no_longer_claims_the_reply_quotes_worker_ids():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    prompt = (root / "intelligence/agents/contract_labor_specialist/prompt.md").read_text(encoding="utf-8")
    tools = (root / "entities/contract_labor/intelligence/tools.py").read_text(encoding="utf-8")
    assert "the reply quotes its worker table" not in prompt
    assert "the reply quotes that email's worker table" not in tools
    assert "stop and report" in prompt.lower()
    assert "never guess" in prompt.lower() or "never guess" in tools.lower()


# --- Pass 2 refactor: direct tests on the extracted helpers, plus the NFC nit ------------------

def test_select_listed_workers_selects_by_id_and_name_and_reports_the_rest():
    from entities.contract_labor.business import service as s
    crew = [{"contract_labor_id": 1, "contract_labor_public_id": _pub(1), "employee_name": "Ann"},
            {"contract_labor_id": 4, "contract_labor_public_id": _pub(4), "employee_name": "Dee"},
            {"contract_labor_id": 6, "contract_labor_public_id": _pub(6), "employee_name": "Dee"},
            {"contract_labor_id": 2, "contract_labor_public_id": _pub(2), "employee_name": "Ben"}]
    listed = [(_pub(2), _pub(2)), (_pub(99), _pub(99))]
    names = [("ann", "ann"), ("dee", "Dee"), ("nobody", "Nobody")]
    decision_crew, not_applied = s._select_listed_workers(crew, listed, names)
    assert [m["contract_labor_public_id"] for m in decision_crew] == [_pub(1), _pub(2)]
    assert not_applied == [
        {"contract_labor_public_id": _pub(99), "employee_name": None, "reason": NOT_REVIEWABLE},
        {"contract_labor_public_id": None, "employee_name": "Dee", "reason": "ambiguous name — name the worker by id"},
        {"contract_labor_public_id": None, "employee_name": "Nobody", "reason": NOT_REVIEWABLE},
    ]


def test_select_listed_workers_with_no_lists_selects_nothing():
    from entities.contract_labor.business import service as s
    assert s._select_listed_workers([_named(ANN, "Ann")], None, None) == ([], [])


def test_applied_none_error_has_the_one_message_shape():
    from entities.contract_labor.business import service as s
    err = s._applied_none_error(3, "proj-1", "2026-10-07", ["a: x", "b: y"])
    assert isinstance(err, ValueError)
    assert str(err) == "apply_reviewer_decision applied none of the 3 crew member(s) on project proj-1 for 2026-10-07: a: x; b: y"


def test_member_label_tolerates_a_missing_id_or_name():
    from entities.contract_labor.business import service as s
    assert s._member_label({"contract_labor_public_id": None, "employee_name": "Ann"}) == "Ann"
    assert s._member_label({"contract_labor_public_id": "pub-1", "employee_name": None}) == "pub-1"
    assert s._member_label({"contract_labor_public_id": "pub-1", "employee_name": "Ann"}) == "pub-1 (Ann)"


def test_worker_names_match_across_unicode_normalisation_forms(st_orch):
    service, st = st_orch
    st.bound = False
    st.crew = [{"contract_labor_id": ANN, "contract_labor_public_id": _pub(ANN), "employee_name": "José"}]
    _call(service, names=["José"])  # decomposed form of the same name
    assert _applied(st) == [_pub(ANN)]


def test_normalise_listed_is_one_validate_and_dedupe_path():
    from entities.contract_labor.business import service as s
    ids = s._normalize_listed(
        [_pub(ANN).upper(), _pub(ANN)], not_a_list=s._IDS_NOT_A_LIST,
        canonicalise=s._public_id_key, invalid=s._IDS_MALFORMED.format,
    )
    assert ids == [(_pub(ANN), _pub(ANN).upper())]
    with pytest.raises(ValueError, match="malformed id: 'nope'"):
        s._normalize_listed(["nope"], not_a_list=s._IDS_NOT_A_LIST,
                            canonicalise=s._public_id_key, invalid=s._IDS_MALFORMED.format)
