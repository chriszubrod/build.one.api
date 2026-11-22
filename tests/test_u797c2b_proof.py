"""U-797c2b item 4: the proof labels subject-driven decision changes SUBJECT_FALLBACK, and the sproc returns Subject.

Static and pure. The proof script is loaded from its file; nothing is run against a database.
"""
from __future__ import annotations

import importlib.util
from types import SimpleNamespace

from tests.sproc_text import REPO_ROOT

_PATH = REPO_ROOT / "scripts" / "u797c1_rebind_proof.py"
_spec = importlib.util.spec_from_file_location("u797c1_rebind_proof_c2b", _PATH)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)

SQL_PATH = REPO_ROOT / "entities" / "review" / "sql" / "dbo.review_notification.sql"


def _n(nid, *, kind="bill", project_id=None, work_date=None, subject="Bill #5 review"):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date, Subject=subject)


CL_RESOLVER = {"match_kind": "conversation", "project_id": 1, "work_date": "2026-11-19"}


def _cl_notif(nid, *, subject="Review: CL - Proj - 2026-11-19"):
    return _n(nid, kind="contract_labor", project_id=1, work_date="2026-11-19", subject=subject)


# ---- labels, passes, exit code -------------------------------------------------
def test_subject_fallback_is_a_label_and_passes_only_when_legacy_binds():
    assert "SUBJECT_FALLBACK" in proof.LABELS
    assert proof.passes("SUBJECT_FALLBACK", "AGREE|legacy_binds") is True
    assert proof.passes("SUBJECT_FALLBACK", "AGREE|legacy_unproven") is False


def test_exit_code_follows_subject_fallback_passing_rule():
    assert proof.exit_code([("SUBJECT_FALLBACK", "AGREE|legacy_binds")]) == 0
    assert proof.exit_code([("SUBJECT_FALLBACK", "AGREE|legacy_unproven")]) == 1


# ---- classify_applied: Bill / Expense ------------------------------------------------
def test_bill_subject_mismatch_changes_the_decision_to_subject_fallback_unproven():
    got = proof.classify_applied(
        kind="bill", reply_conversation_id="Conv-A", reply_subject="Bill #5 review",
        matches=[(_n(1, subject="Some other thread"), {("bill", 5)})],
        expected=("bill", 5), pre_u579=False)
    assert got == ("SUBJECT_FALLBACK", "AGREE|legacy_unproven")


def test_bill_equal_subject_is_unchanged_agree():
    got = proof.classify_applied(
        kind="bill", reply_conversation_id="Conv-A", reply_subject="RE: bill #5 review",
        matches=[(_n(1, subject="Bill #5 review"), {("bill", 5)})],
        expected=("bill", 5), pre_u579=False)
    assert got == ("AGREE", "bound")


def test_bill_without_a_reply_subject_is_unchanged():
    got = proof.classify_applied(
        kind="bill", reply_conversation_id="Conv-A", reply_subject=None,
        matches=[(_n(1, subject="other"), {("bill", 5)})],
        expected=("bill", 5), pre_u579=False)
    assert got == ("AGREE", "bound")


def test_bill_refusal_turned_into_legacy_fallback_by_subject_is_subject_fallback_and_fails():
    # The record refused (wrong document). A subject mismatch turns that into a legacy fallback,
    # which is a changed decision, so it is labelled. Bill legacy scope is not provable: fails.
    got = proof.classify_applied(
        kind="bill", reply_conversation_id="Conv-A", reply_subject="x",
        matches=[(_n(1, subject="other"), {("bill", 9)})],
        expected=("bill", 5), pre_u579=False)
    assert got == ("SUBJECT_FALLBACK", "DISAGREE|legacy_unproven")
    assert proof.passes(*got) is False


def test_cl_listed_row_pushed_to_legacy_by_subject_is_not_a_silent_pass():
    # The applied record is outside the legacy scope (LISTED without the subject). A subject
    # mismatch sends the reply to a legacy binding that cannot reach that record: must not pass.
    got = proof.classify_applied(
        kind="contract_labor", reply_conversation_id="Conv-A", reply_subject="Retitled",
        matches=[(_cl_notif(1, subject="other"), {("contract_labor", 7)})],
        expected=("contract_labor", 7), pre_u579=False, resolver_result=CL_RESOLVER,
        cl_work_date="2026-11-18", cl_project_ids={1})
    assert got[0] == "SUBJECT_FALLBACK"
    assert got[1].endswith("|legacy_unproven")
    assert proof.passes(*got) is False


# ---- classify_applied: Contract Labor ------------------------------------------------
def test_cl_subject_mismatch_inside_legacy_scope_is_subject_fallback_that_legacy_binds():
    got = proof.classify_applied(
        kind="contract_labor", reply_conversation_id="Conv-A",
        reply_subject="Retitled draft", matches=[(_cl_notif(1, subject="Review: CL - Proj - 2026-11-19"), {("contract_labor", 7)})],
        expected=("contract_labor", 7), pre_u579=False, resolver_result=CL_RESOLVER,
        cl_work_date="2026-11-19", cl_project_ids={1})
    assert got == ("SUBJECT_FALLBACK", "AGREE|legacy_binds")
    assert proof.passes(*got) is True


def test_cl_equal_subject_is_unchanged():
    got = proof.classify_applied(
        kind="contract_labor", reply_conversation_id="Conv-A",
        reply_subject="Review: CL - Proj - 2026-11-19", matches=[(_cl_notif(1), {("contract_labor", 7)})],
        expected=("contract_labor", 7), pre_u579=False, resolver_result=CL_RESOLVER,
        cl_work_date="2026-11-19", cl_project_ids={1})
    assert got == ("AGREE", "bound")


def test_cl_subject_mismatch_outside_legacy_scope_fails():
    got = proof.classify_applied(
        kind="contract_labor", reply_conversation_id="Conv-A",
        reply_subject="Retitled draft", matches=[(_cl_notif(1, subject="other"), {("contract_labor", 7)})],
        expected=("contract_labor", 7), pre_u579=False, resolver_result=None,
        cl_work_date="2026-11-19", cl_project_ids={1})
    # No legacy scope: without the subject the new binder would bind (NEW_BIND); with the
    # subject it falls back to a legacy that cannot scope the reply. That is a refusal: it fails.
    assert got == ("SUBJECT_FALLBACK", "NEW_BIND|legacy_unproven")
    assert proof.passes(*got) is False


def test_subject_fallback_listed_rows_are_counted_under_their_own_label():
    counts = proof.summarize([("SUBJECT_FALLBACK", "AGREE|legacy_binds"), ("AGREE", "bound")])
    assert counts["SUBJECT_FALLBACK"] == 1
    assert counts["AGREE"] == 1


# ---- sproc returns Subject and the proof reads the reply's Subject ---------------
def _find_sproc_body() -> str:
    text = SQL_PATH.read_text(encoding="utf-8")
    start = text.index("CREATE OR ALTER PROCEDURE dbo.FindReviewNotificationForReply")
    end = text.index("GO", start)
    return text[start:end]


def test_find_sproc_target_result_set_unchanged_in_shape():
    body = _find_sproc_body()
    assert "t.[ReviewNotificationId]" in body
    assert "[ContractLaborId]" in body


def test_sweep_reads_the_reply_subject():
    assert "ReplySubject" in proof.SWEEP_SQL
    assert "m.[Subject]" in proof.SWEEP_SQL


# ---- fix round 2: SUBJECT_FALLBACK only when the live selector itself reports subject_mismatch ----
def test_cl_two_mismatched_matches_stay_ambiguous_and_fail():
    # Live selector: two matches -> ambiguous, the guard never rescues it. Must not be SUBJECT_FALLBACK.
    matches = [(_cl_notif(1, subject="a"), {("contract_labor", 7)}),
               (_cl_notif(2, subject="b"), {("contract_labor", 7)})]
    got = proof.classify_applied(
        kind="contract_labor", reply_conversation_id="Conv-A", reply_subject="Retitled",
        matches=matches, expected=("contract_labor", 7), pre_u579=False,
        resolver_result=CL_RESOLVER, cl_work_date="2026-11-19", cl_project_ids={1})
    assert got == ("AMBIGUOUS", "ambiguous")
    assert proof.passes(*got) is False


def test_cl_wrong_kind_with_mismatch_keeps_its_base_label():
    # Live selector: wrong_kind is never rescued by the subject. The base label stands.
    matches = [(_n(1, kind="bill", subject="other"), {("contract_labor", 7)})]
    got = proof.classify_applied(
        kind="contract_labor", reply_conversation_id="Conv-A", reply_subject="Retitled",
        matches=matches, expected=("contract_labor", 7), pre_u579=False,
        resolver_result=CL_RESOLVER, cl_work_date="2026-11-19", cl_project_ids={1})
    assert got == ("DISAGREE", "wrong_kind")
    assert proof.passes(*got) is False


def test_bill_two_mismatched_matches_stay_ambiguous():
    two = [(_n(1, subject="a"), {("bill", 5)}), (_n(2, subject="b"), {("bill", 5)})]
    got = proof.classify_applied(
        kind="bill", reply_conversation_id="Conv-A", reply_subject="Retitled",
        matches=two, expected=("bill", 5), pre_u579=False)
    assert got == ("AMBIGUOUS", "ambiguous")
