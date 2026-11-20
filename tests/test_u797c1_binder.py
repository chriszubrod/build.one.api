"""U-797c1: the notification binder, the flag-gated apply paths, and the legacy window constant."""
from __future__ import annotations

import inspect
from datetime import date
from types import SimpleNamespace

import pytest

from entities.review.business import review_notification_service as rns
from entities.review.business import reviewer_decision as rd
from entities.contract_labor.business import service as cl_service_mod
from shared.authz import provenance as prov
from shared.authz.provenance import ReviewerProvenanceRefused


def _em(conv="Conv-A", folder="inbox", sender="pm@x.test", pid="EM1"):
    return SimpleNamespace(id=1, public_id=pid, conversation_id=conv, folder=folder, from_address=sender)


class FakeLookup:
    """Replaces ReviewNotificationRecordService.lookup_for_reply; matches EXACTLY (case-sensitive)."""

    def __init__(self, table):
        self.table = table  # {conversation_id: [(notif, {targets})]}
        self.seen: list = []

    def __call__(self, conversation_id):
        self.seen.append(conversation_id)
        return list(self.table.get(conversation_id, []))


def _notif(nid):
    return SimpleNamespace(Id=nid)


def _cl_notif(nid, *, project_id=1, work_date="2026-11-19", kind="contract_labor"):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date)


@pytest.fixture
def lookup(monkeypatch):
    holder = {}

    def install(table):
        fake = FakeLookup(table)
        monkeypatch.setattr(rns.ReviewNotificationRecordService, "lookup_for_reply", fake)
        holder["fake"] = fake
        return fake

    return install


# ---------------------------------------------------------------------------
# bind_reply_via_notification: exactly-one rule, target-membership rule, BIN match
# ---------------------------------------------------------------------------
def test_no_row_returns_none(lookup):
    lookup({})
    assert prov.bind_reply_via_notification("Conv-A", ("bill", 5)) is None


def test_two_rows_is_ambiguous_and_refuses(lookup):
    lookup({"Conv-A": [(_notif(1), {("bill", 5)}), (_notif(2), {("bill", 5)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification("Conv-A", ("bill", 5))


def test_one_row_containing_the_target_binds(lookup):
    n = _notif(1)
    lookup({"Conv-A": [(n, {("bill", 5), ("bill", 6)})]})
    assert prov.bind_reply_via_notification("Conv-A", ("bill", 6)) is n


def test_one_row_excluding_the_target_refuses_not_none(lookup):
    lookup({"Conv-A": [(_notif(1), {("bill", 5)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification("Conv-A", ("bill", 6))


def test_target_kind_must_match_not_just_the_id(lookup):
    lookup({"Conv-A": [(_notif(1), {("expense", 5)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification("Conv-A", ("bill", 5))


def test_conversation_match_is_exact_and_case_sensitive(lookup):
    fake = lookup({"Conv-A": [(_notif(1), {("bill", 5)})]})
    assert prov.bind_reply_via_notification("conv-a", ("bill", 5)) is None
    assert prov.bind_reply_via_notification(" Conv-A", ("bill", 5)) is None
    assert fake.seen == ["conv-a", " Conv-A"]  # passed through unmodified, never normalised
    assert prov.bind_reply_via_notification("Conv-A", ("bill", 5)) is not None


def test_blank_conversation_is_no_row_without_a_lookup(lookup):
    fake = lookup({})
    assert prov.bind_reply_via_notification(None, ("bill", 5)) is None
    assert prov.bind_reply_via_notification("", ("bill", 5)) is None
    assert fake.seen == []


# ---------------------------------------------------------------------------
# bind_reviewer_reply_with_notification: the flag-gated decision
# ---------------------------------------------------------------------------
@pytest.fixture
def flag(monkeypatch):
    def set_flag(on: bool):
        monkeypatch.setattr(prov, "_binder_v2_enabled", lambda: on)
    return set_flag


@pytest.fixture
def generic(monkeypatch):
    """Stub the shared generic checks (inbox, From) so tests reach the binder."""
    calls = []
    em = _em()

    def fake_bind_reviewer_reply(**kw):
        calls.append(("generic", kw))
        return em

    monkeypatch.setattr(prov, "bind_reviewer_reply", fake_bind_reviewer_reply)
    return SimpleNamespace(calls=calls, em=em)


def _legacy(result=None, raises=None):
    calls = []

    def legacy():
        calls.append("legacy")
        if raises:
            raise raises
        return result if result is not None else "LEGACY_EM"

    return legacy, calls


def test_flag_off_calls_legacy_only(monkeypatch, flag, generic, lookup):
    flag(False)
    fake = lookup({"Conv-A": [(_notif(1), {("bill", 5)})]})
    legacy, lcalls = _legacy()
    got = prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=("bill", 5), what="a bill reviewer decision", legacy=legacy)
    assert got == "LEGACY_EM"
    assert lcalls == ["legacy"]
    assert fake.seen == []  # the new binder is not consulted
    assert generic.calls == []  # OFF: the generic checks run inside legacy, as today


def test_flag_on_bound_row_returns_the_reply_and_skips_legacy(flag, generic, lookup):
    flag(True)
    lookup({"Conv-A": [(_notif(1), {("bill", 5)})]})
    legacy, lcalls = _legacy()
    got = prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=("bill", 5), what="w", legacy=legacy)
    assert got is generic.em
    assert lcalls == []


def test_flag_on_no_row_falls_back_to_legacy(flag, generic, lookup):
    flag(True)
    lookup({})
    legacy, lcalls = _legacy()
    got = prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=("bill", 5), what="w", legacy=legacy)
    assert got == "LEGACY_EM"
    assert lcalls == ["legacy"]


def test_flag_on_row_excluding_target_rejects_with_no_fallback(flag, generic, lookup):
    flag(True)
    lookup({"Conv-A": [(_notif(1), {("bill", 9)})]})
    legacy, lcalls = _legacy()
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reviewer_reply_with_notification(
            reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
            target=("bill", 5), what="w", legacy=legacy)
    assert lcalls == []


def test_flag_on_ambiguous_rejects_with_no_fallback(flag, generic, lookup):
    flag(True)
    lookup({"Conv-A": [(_notif(1), {("bill", 5)}), (_notif(2), {("bill", 5)})]})
    legacy, lcalls = _legacy()
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reviewer_reply_with_notification(
            reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
            target=("bill", 5), what="w", legacy=legacy)
    assert lcalls == []


def test_flag_on_generic_checks_still_run_first(monkeypatch, flag, lookup):
    flag(True)
    fake = lookup({"Conv-A": [(_notif(1), {("bill", 5)})]})

    def refuse(**kw):
        raise ReviewerProvenanceRefused("Cannot apply w: the reply must be an inbound inbox message")

    monkeypatch.setattr(prov, "bind_reviewer_reply", refuse)
    legacy, lcalls = _legacy()
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reviewer_reply_with_notification(
            reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
            target=("bill", 5), what="w", legacy=legacy)
    assert fake.seen == []
    assert lcalls == []


def test_flag_default_is_off_in_settings():
    from config import Settings

    field = Settings.model_fields["review_reply_binder_v2_enabled"]
    assert field.default is False


def test_legacy_retire_constant_is_sixty_days_after_c1():
    assert prov.LEGACY_BINDING_RETIRE_AFTER == date(2027, 1, 19)


# ---------------------------------------------------------------------------
# Apply paths: call sites route through the shared helper; flag-off is today's path
# ---------------------------------------------------------------------------
def test_bill_expense_apply_emailed_routes_through_the_notification_binder():
    src = inspect.getsource(rd.apply_emailed)
    assert "bind_reviewer_reply_with_notification(" in src
    assert "target=(spec.parent, parent.id)" in src
    # The legacy binder is still the fallback, unchanged.
    assert "bind_reviewer_reply_to_review_thread(" in src
    assert src.index("bind_reviewer_reply_with_notification(") < src.index("bind_reviewer_reply_to_review_thread(")


def test_cl_apply_paths_both_use_the_helper():
    crew_src = inspect.getsource(cl_service_mod.ContractLaborService.apply_reviewer_decision)
    single_src = inspect.getsource(cl_service_mod.ContractLaborService._apply_decision_to_single_cl)
    assert "self._bind_cl_decision_reply(" in crew_src
    assert "self._bind_cl_decision_reply(" in single_src
    assert "bind_reviewer_reply_to_thread(" not in crew_src
    assert "bind_reviewer_reply_to_thread(" not in single_src


def _cl_self(monkeypatch, legacy_expected="CONV-LEGACY"):
    svc = cl_service_mod.ContractLaborService.__new__(cl_service_mod.ContractLaborService)
    calls = []
    monkeypatch.setattr(
        svc, "_expected_cl_reviewer_reply_conversation_id",
        lambda **kw: (calls.append("expected") or legacy_expected),
    )
    return svc, calls


def test_cl_helper_flag_off_is_today_s_binding(monkeypatch, flag, lookup):
    flag(False)
    fake = lookup({"Conv-A": [(_notif(1), {("contract_labor", 7)})]})
    seen = []

    def legacy_bind(**kw):
        seen.append(kw["expected_conversation_id"])
        return "LEGACY_EM"

    monkeypatch.setattr(prov, "bind_reviewer_reply_to_thread", legacy_bind)
    svc, calls = _cl_self(monkeypatch)
    got = svc._bind_cl_decision_reply(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        contract_labor_id=7, contract_labor_public_id="cp", project_public_id="pp", project_id=1,
        work_date="2026-11-19")
    assert got == "LEGACY_EM"
    assert seen == ["CONV-LEGACY"]
    assert calls == ["expected"]
    assert fake.seen == []


def test_cl_helper_flag_on_bound_row_skips_legacy_expected_lookup(monkeypatch, flag, lookup, generic):
    flag(True)
    lookup({"Conv-A": [(_cl_notif(1), {("contract_labor", 7)})]})
    monkeypatch.setattr(prov, "bind_reviewer_reply_to_thread",
                        lambda **kw: pytest.fail("legacy must not run when a row binds"))
    svc, calls = _cl_self(monkeypatch)
    got = svc._bind_cl_decision_reply(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        contract_labor_id=7, contract_labor_public_id="cp", project_public_id="pp", project_id=1,
        work_date="2026-11-19")
    assert got is generic.em
    assert calls == []


def test_cl_helper_flag_on_no_row_uses_legacy(monkeypatch, flag, lookup, generic):
    flag(True)
    lookup({})
    seen = []
    monkeypatch.setattr(prov, "bind_reviewer_reply_to_thread",
                        lambda **kw: seen.append(kw["expected_conversation_id"]) or "LEGACY_EM")
    svc, calls = _cl_self(monkeypatch)
    got = svc._bind_cl_decision_reply(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        contract_labor_id=7, contract_labor_public_id="cp", project_public_id="pp", project_id=1,
        work_date="2026-11-19")
    assert got == "LEGACY_EM"
    assert seen == ["CONV-LEGACY"]


def test_cl_helper_flag_on_row_for_another_project_rejects(monkeypatch, flag, lookup, generic):
    flag(True)
    lookup({"Conv-A": [(_cl_notif(1, project_id=2), {("contract_labor", 7)})]})
    monkeypatch.setattr(prov, "bind_reviewer_reply_to_thread",
                        lambda **kw: pytest.fail("no fallback when a row exists"))
    svc, _ = _cl_self(monkeypatch)
    with pytest.raises(ReviewerProvenanceRefused):
        svc._bind_cl_decision_reply(
            reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
            contract_labor_id=7, contract_labor_public_id="cp", project_public_id="pp", project_id=1,
            work_date="2026-11-19")


def test_crew_loop_passes_each_members_own_id(monkeypatch):
    """The crew loop binds every member against that member's own ContractLabor id."""
    svc = cl_service_mod.ContractLaborService.__new__(cl_service_mod.ContractLaborService)
    seen_ids = []
    monkeypatch.setattr(svc, "_bind_cl_decision_reply",
                        lambda **kw: seen_ids.append(kw["contract_labor_id"]) or SimpleNamespace(id=1))
    monkeypatch.setattr(svc, "_apply_decision_to_single_cl", lambda **kw: {"review_status": "x"})

    class Proj:
        id = 1

    monkeypatch.setattr(cl_service_mod.ProjectService, "read_by_public_id",
                        lambda self, public_id: Proj())
    svc.repo = SimpleNamespace(read_reviewable_crew_by_project_and_date=lambda **kw: [
        {"contract_labor_id": 7, "contract_labor_public_id": "a"},
        {"contract_labor_id": 8, "contract_labor_public_id": "b"},
    ])
    try:
        svc.apply_reviewer_decision(project_public_id="pp", work_date="2026-11-19", decision="rejected",
                                    reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1")
    except Exception:
        pass  # the post-bind loop is not under test here; the binding calls are
    assert seen_ids == [7, 8]


# ---------------------------------------------------------------------------
# Fix round 1, items 1-2: the CL rule is project + date + kind, never target membership.
# ---------------------------------------------------------------------------
def test_cl_late_added_member_binds_when_project_and_date_match(lookup):
    n = _cl_notif(1, project_id=1, work_date="2026-11-19")
    lookup({"Conv-A": [(n, {("contract_labor", 7)})]})  # member 8 was added after the send
    got = prov.bind_reply_via_notification(
        "Conv-A", ("contract_labor", 8), project_id=1, work_date="2026-11-19")
    assert got is n


def test_cl_cross_project_reply_is_refused(lookup):
    # One CL is on the notification for project 2; the reply applies it on project 1.
    lookup({"Conv-A": [(_cl_notif(1, project_id=2), {("contract_labor", 7)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification(
            "Conv-A", ("contract_labor", 7), project_id=1, work_date="2026-11-19")


def test_cl_wrong_date_is_refused(lookup):
    lookup({"Conv-A": [(_cl_notif(1, work_date="2026-11-18"), {("contract_labor", 7)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification(
            "Conv-A", ("contract_labor", 7), project_id=1, work_date="2026-11-19")


def test_cl_wrong_kind_is_refused(lookup):
    lookup({"Conv-A": [(_cl_notif(1, kind="bill"), {("contract_labor", 7)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification(
            "Conv-A", ("contract_labor", 7), project_id=1, work_date="2026-11-19")


def test_cl_without_project_or_date_context_is_refused(lookup):
    lookup({"Conv-A": [(_cl_notif(1), {("contract_labor", 7)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification("Conv-A", ("contract_labor", 7))


def test_cl_date_compares_iso_string_and_date_object(lookup):
    from datetime import date as _date
    n = _cl_notif(1, work_date=_date(2026, 11, 19))
    lookup({"Conv-A": [(n, {("contract_labor", 7)})]})
    assert prov.bind_reply_via_notification(
        "Conv-A", ("contract_labor", 7), project_id=1, work_date="2026-11-19") is n


def test_bill_still_uses_target_membership_with_no_scope_args(lookup):
    n = _notif(1)
    lookup({"Conv-A": [(n, {("bill", 5)})]})
    assert prov.bind_reply_via_notification("Conv-A", ("bill", 5)) is n


def test_flag_on_cl_helper_forwards_project_and_date_to_the_binder(flag, generic, lookup):
    flag(True)
    n = _cl_notif(1, project_id=1, work_date="2026-11-19")
    lookup({"Conv-A": [(n, {("contract_labor", 7)})]})
    legacy, lcalls = _legacy()
    got = prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=("contract_labor", 8), what="w", legacy=legacy,
        project_id=1, work_date="2026-11-19")
    assert got is generic.em
    assert lcalls == []


def test_crew_loop_passes_the_work_date_to_each_member_bind(monkeypatch):
    svc = cl_service_mod.ContractLaborService.__new__(cl_service_mod.ContractLaborService)
    seen = []
    monkeypatch.setattr(svc, "_bind_cl_decision_reply",
                        lambda **kw: seen.append((kw["contract_labor_id"], kw["work_date"])) or SimpleNamespace(id=1))
    monkeypatch.setattr(svc, "_apply_decision_to_single_cl", lambda **kw: {"review_status": "x"})
    monkeypatch.setattr(cl_service_mod.ProjectService, "read_by_public_id",
                        lambda self, public_id: SimpleNamespace(id=1))
    svc.repo = SimpleNamespace(read_reviewable_crew_by_project_and_date=lambda **kw: [
        {"contract_labor_id": 7, "contract_labor_public_id": "a"},
    ])
    try:
        svc.apply_reviewer_decision(project_public_id="pp", work_date="2026-11-19", decision="rejected",
                                    reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1")
    except Exception:
        pass
    assert seen == [(7, "2026-11-19")]



# ---------------------------------------------------------------------------
# Fix round 2, item 2 (N2): work dates parse to date objects, and fail closed.
# ---------------------------------------------------------------------------
def test_norm_work_date_compares_date_datetime_and_iso_string_as_equal():
    from datetime import datetime as _dt
    target = date(2026, 11, 16)
    assert prov._norm_work_date(date(2026, 11, 16)) == target
    assert prov._norm_work_date(_dt(2026, 11, 16, 9, 30)) == target
    assert prov._norm_work_date("2026-11-16") == target
    assert prov._norm_work_date("2026-11-16 00:00:00") == target
    assert prov._norm_work_date("2026-11-16T00:00:00") == target


def test_norm_work_date_fails_closed_on_unparseable_input():
    assert prov._norm_work_date("2026-11-5") is None
    assert prov._norm_work_date("11/20/2026") is None
    assert prov._norm_work_date("garbage") is None
    assert prov._norm_work_date("") is None
    assert prov._norm_work_date(None) is None


def test_cl_refuses_when_both_dates_are_unparseable(lookup):
    # Equal but unparseable strings must not bind: fail closed.
    lookup({"Conv-A": [(_cl_notif(1, work_date="11/20/2026"), {("contract_labor", 7)})]})
    with pytest.raises(ReviewerProvenanceRefused):
        prov.bind_reply_via_notification(
            "Conv-A", ("contract_labor", 7), project_id=1, work_date="11/20/2026")
