"""U-797c2b item 1: the retitled-draft guard. A subject mismatch falls back to legacy; it never binds.

Pure fakes only. No database, Graph or HTTP.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from entities.review.business import review_notification_service as rns
from shared.authz import provenance as prov


def _n(nid, *, subject="Bill #5 review", kind="bill", project_id=None, work_date=None):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date, Subject=subject)


def _cl_n(nid, *, subject="Review: CL - Proj - 2026-11-19", project_id=1, work_date="2026-11-19", kind="contract_labor"):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date, Subject=subject)


BILL5 = ("bill", 5)
CL7 = ("contract_labor", 7)


# ---------------------------------------------------------------------------
# normalize_subject
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Re: Bill #5", "bill #5"),
        ("RE: Bill #5", "bill #5"),
        ("FW: Bill #5", "bill #5"),
        ("Fwd: Bill #5", "bill #5"),
        ("fwd:Bill #5", "bill #5"),
        ("  re :  Bill #5", "bill #5"),
        ("Re: Re: FW: Fwd:  Bill #5", "bill #5"),
        ("   Bill     #5   ", "bill #5"),
        ("BILL #5", "bill #5"),
        ("Review: Bill", "review: bill"),  # "Review:" is not a reply prefix
        ("Fwd: Review: Bill", "review: bill"),
        ("", None),
        ("   ", None),
        ("Re:", None),
        ("RE: ", None),
        (None, None),
    ],
)
def test_normalize_subject(raw, expected):
    assert prov.normalize_subject(raw) == expected


# ---------------------------------------------------------------------------
# select_notification with reply_subject: only the safe direction changes
# ---------------------------------------------------------------------------
def test_subject_mismatch_on_bill_falls_back_to_no_row_even_where_it_would_bind():
    matches = [(_n(1, subject="Totally different thread"), {BILL5})]
    assert prov.select_notification(matches, BILL5)[0] == "BOUND"  # no subject: unchanged
    assert prov.select_notification(matches, BILL5, reply_subject="Bill #5 review") == (
        "NO_ROW", "subject_mismatch", None)


def test_subject_mismatch_on_cl_falls_back_even_where_project_refuses():
    matches = [(_cl_n(1, subject="Some other CL subject", project_id=2), {CL7})]
    assert prov.select_notification(matches, CL7, project_id=1, work_date="2026-11-19")[1] == (
        "wrong_project_or_date")
    assert prov.select_notification(matches, CL7, project_id=1, work_date="2026-11-19",
                                    reply_subject="Review: CL - Proj - 2026-11-19") == (
        "NO_ROW", "subject_mismatch", None)


def test_subject_mismatch_never_rescues_wrong_kind():
    # U-797c2b fix round 1 (F1): wrong_kind is a refusal the subject guard does not override.
    matches = [(_cl_n(1, subject="other", kind="bill"), {CL7})]
    assert prov.select_notification(matches, CL7, project_id=1, work_date="2026-11-19")[1] == "wrong_kind"
    assert prov.select_notification(matches, CL7, project_id=1, work_date="2026-11-19",
                                    reply_subject="Review: CL")[:2] == ("REFUSED", "wrong_kind")


def test_subject_mismatch_rescues_wrong_document():
    # F1: wrong_document is one of the two refusals the guard rescues (falls back to legacy).
    matches = [(_n(1, subject="other"), {("bill", 9)})]
    assert prov.select_notification(matches, BILL5, reply_subject="Bill #5 review")[:2] == (
        "NO_ROW", "subject_mismatch")


def test_equal_subject_after_prefix_normalisation_keeps_today_decision():
    matches = [(_n(1, subject="Bill #5 review"), {BILL5})]
    assert prov.select_notification(matches, BILL5, reply_subject="RE: bill #5  REVIEW")[0] == "BOUND"


def test_equal_subject_keeps_refusal_decision_too():
    matches = [(_n(1, subject="Bill #5 review"), {("bill", 9)})]
    assert prov.select_notification(matches, BILL5, reply_subject="Bill #5 review")[:2] == (
        "REFUSED", "wrong_document")


def test_match_with_none_subject_is_unchanged():
    matches = [(_n(1, subject=None), {BILL5})]
    assert prov.select_notification(matches, BILL5, reply_subject="Anything else")[0] == "BOUND"


def test_one_none_subject_among_mismatching_matches_is_unchanged():
    matches = [(_n(1, subject="other"), {BILL5}), (_n(2, subject=None), {BILL5})]
    # Two matches: ambiguous, as today. The None subject stops the subject check.
    assert prov.select_notification(matches, BILL5, reply_subject="Bill #5 review")[:2] == (
        "REFUSED", "ambiguous")


def test_reply_subject_none_or_blank_is_unchanged():
    matches = [(_n(1, subject="other"), {BILL5})]
    assert prov.select_notification(matches, BILL5, reply_subject=None)[0] == "BOUND"
    assert prov.select_notification(matches, BILL5, reply_subject="   ")[0] == "BOUND"
    assert prov.select_notification(matches, BILL5, reply_subject="Re:")[0] == "BOUND"


def test_two_matches_one_equal_is_unchanged_still_ambiguous_no_filtering():
    matches = [(_n(1, subject="Bill #5 review"), {BILL5}), (_n(2, subject="other"), {BILL5})]
    assert prov.select_notification(matches, BILL5, reply_subject="Bill #5 review")[:2] == (
        "REFUSED", "ambiguous")


def test_two_matches_both_mismatch_stays_ambiguous_not_no_row():
    # F1: ambiguous is never overridden by the subject guard. Two records still refuse.
    matches = [(_n(1, subject="a"), {BILL5}), (_n(2, subject="b"), {BILL5})]
    assert prov.select_notification(matches, BILL5, reply_subject="Bill #5 review")[:2] == (
        "REFUSED", "ambiguous")


def test_no_matches_is_no_row_no_row():
    assert prov.select_notification([], BILL5, reply_subject="x") == ("NO_ROW", "no_row", None)


# ---------------------------------------------------------------------------
# bind_reply_via_notification: the binder carries reply_subject and logs ids + reason only
# ---------------------------------------------------------------------------
@pytest.fixture
def lookup(monkeypatch):
    def install(table):
        def fake(self, conversation_id):
            return list(table.get(conversation_id, []))
        monkeypatch.setattr(rns.ReviewNotificationRecordService, "lookup_for_reply", fake)
    return install


def test_binder_subject_mismatch_returns_none_so_the_caller_uses_legacy(lookup):
    lookup({"Conv-A": [(_n(11, subject="Other thread"), {BILL5})]})
    assert prov.bind_reply_via_notification("Conv-A", BILL5, reply_subject="Bill #5 review") is None


def test_binder_without_reply_subject_is_unchanged(lookup):
    n = _n(11, subject="Other thread")
    lookup({"Conv-A": [(n, {BILL5})]})
    assert prov.bind_reply_via_notification("Conv-A", BILL5) is n


def test_binder_subject_mismatch_logs_ids_and_reason_never_subject_text(lookup, caplog):
    lookup({"Conv-A": [(_n(11, subject="SECRET-SUBJECT-TEXT"), {BILL5})]})
    with caplog.at_level(logging.WARNING, logger=prov.logger.name):
        prov.bind_reply_via_notification("Conv-A", BILL5, reply_subject="Reply-SECRET-TEXT")
    records = [r for r in caplog.records if r.name == prov.logger.name]
    assert records, "a subject mismatch must be logged"
    blob = " ".join(r.getMessage() + " " + repr(r.__dict__) for r in records)
    assert "subject_mismatch" in blob
    assert "11" in blob
    assert "SECRET-SUBJECT-TEXT" not in blob
    assert "Reply-SECRET-TEXT" not in blob


@pytest.fixture
def flag(monkeypatch):
    def set_flag(on: bool):
        monkeypatch.setattr(prov, "_binder_v2_enabled", lambda: on)
    return set_flag


def _em(subject, conv="Conv-A"):
    return SimpleNamespace(id=1, public_id="EM1", conversation_id=conv, folder="inbox",
                           from_address="pm@x.test", subject=subject)


@pytest.fixture
def generic(monkeypatch):
    def install(em):
        monkeypatch.setattr(prov, "bind_reviewer_reply", lambda **kw: em)
        return em
    return install


def _legacy():
    calls = []

    def legacy():
        calls.append("legacy")
        return "LEGACY_EM"
    return legacy, calls


def test_flag_on_subject_mismatch_falls_back_to_legacy_with_no_bind(flag, generic, lookup):
    flag(True)
    generic(_em("Bill #5 review"))
    lookup({"Conv-A": [(_n(11, subject="Other thread"), {BILL5})]})
    legacy, calls = _legacy()
    got = prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=BILL5, what="w", legacy=legacy)
    assert got == "LEGACY_EM"
    assert calls == ["legacy"]


def test_flag_on_equal_subject_binds_as_today(flag, generic, lookup):
    flag(True)
    em = generic(_em("RE: Bill #5 review"))
    lookup({"Conv-A": [(_n(11, subject="Bill #5 review"), {BILL5})]})
    legacy, calls = _legacy()
    got = prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=BILL5, what="w", legacy=legacy)
    assert got is em
    assert calls == []


def test_flag_on_reply_without_subject_attribute_is_unchanged(flag, generic, lookup):
    flag(True)
    em = SimpleNamespace(id=1, public_id="EM1", conversation_id="Conv-A", folder="inbox",
                         from_address="pm@x.test")
    generic(em)
    lookup({"Conv-A": [(_n(11, subject="Other thread"), {BILL5})]})
    legacy, calls = _legacy()
    assert prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=BILL5, what="w", legacy=legacy) is em
    assert calls == []


def test_flag_off_never_consults_the_subject_or_the_record(monkeypatch, flag, generic, lookup):
    flag(False)
    generic(_em("Bill #5 review"))
    seen = []
    monkeypatch.setattr(rns.ReviewNotificationRecordService, "lookup_for_reply",
                        lambda self, conversation_id: seen.append(conversation_id) or [])
    legacy, calls = _legacy()
    assert prov.bind_reviewer_reply_with_notification(
        reviewer_email="pm@x.test", reviewer_email_message_public_id="EM1",
        target=BILL5, what="w", legacy=legacy) == "LEGACY_EM"
    assert seen == []
    assert calls == ["legacy"]


def test_subject_mismatch_needs_every_match_to_differ():
    # Pins subject_mismatch directly (an "any differs" mutant is equivalent inside select_notification,
    # which only consults it for single-match decisions). One equal match means no mismatch.
    equal_and_other = [(_n(1, subject="Bill #5 review"), {BILL5}), (_n(2, subject="other"), {BILL5})]
    assert prov.subject_mismatch(equal_and_other, "RE: Bill #5 review") is False
    assert prov.subject_mismatch(list(reversed(equal_and_other)), "RE: Bill #5 review") is False
    both_other = [(_n(1, subject="x"), {BILL5}), (_n(2, subject="y"), {BILL5})]
    assert prov.subject_mismatch(both_other, "RE: Bill #5 review") is True
