"""U-797c1: the re-bind proof classifies with the SAME pure selector the live binder uses."""
from __future__ import annotations

import importlib.util
from datetime import date
from types import SimpleNamespace

from shared.authz.provenance import select_notification
from tests.sproc_text import REPO_ROOT

_PATH = REPO_ROOT / "scripts" / "u797c1_rebind_proof.py"
_spec = importlib.util.spec_from_file_location("u797c1_rebind_proof", _PATH)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)


def _notif(nid, *, kind="bill", project_id=None, work_date=None):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date)


CL_EXPECTED = ("contract_labor", 7)


# ---- The shared selector (also what bind_reply_via_notification uses) ----
def test_selector_no_row_for_no_matches():
    assert select_notification([], ("bill", 5)) == ("NO_ROW", "no_row", None)


def test_selector_refuses_ambiguous_conversations():
    matches = [(_notif(1), {("bill", 5)}), (_notif(2), {("bill", 5)})]
    assert select_notification(matches, ("bill", 5))[:2] == ("REFUSED", "ambiguous")


def test_selector_bill_membership_binds_and_refuses_on_a_missing_document():
    matches = [(_notif(1), {("bill", 5)})]
    assert select_notification(matches, ("bill", 5))[0] == "BOUND"
    assert select_notification(matches, ("bill", 6))[:2] == ("REFUSED", "wrong_document")


def test_selector_cl_rule_is_project_date_kind_not_membership():
    m = [(_notif(1, kind="contract_labor", project_id=1, work_date=date(2026, 11, 19)), {("contract_labor", 7)})]
    # late-added member 8 is not a target but project and date match
    assert select_notification(m, ("contract_labor", 8), project_id=1, work_date="2026-11-19")[0] == "BOUND"
    assert select_notification(m, CL_EXPECTED, project_id=2, work_date="2026-11-19")[:2] == (
        "REFUSED", "wrong_project_or_date")
    assert select_notification(m, CL_EXPECTED, project_id=1, work_date="2026-11-18")[:2] == (
        "REFUSED", "wrong_project_or_date")
    wrong_kind = [(_notif(1, kind="bill", project_id=1, work_date="2026-11-19"), {("contract_labor", 7)})]
    assert select_notification(wrong_kind, CL_EXPECTED, project_id=1, work_date="2026-11-19")[:2] == (
        "REFUSED", "wrong_kind")


# ---- Classification by reason ----
def _classify(**over):
    base = dict(reply_conversation_id="Conv-A", matches=[], expected=("bill", 5),
                project_id=None, work_date=None, pre_u579=False)
    base.update(over)
    return proof.classify_row(**base)


def test_classify_agree_when_the_selector_binds():
    assert _classify(matches=[(_notif(1), {("bill", 5)})]) == ("AGREE", "bound")


def test_classify_no_row_reasons_are_distinct():
    assert _classify(reply_conversation_id=None) == ("NO_ROW", "null_conversation")
    assert _classify() == ("NO_ROW", "no_row")
    assert _classify(pre_u579=True) == ("NO_ROW", "pre_u579_thread")


def test_classify_refusals_are_disagree_or_ambiguous():
    wrong = [(_notif(1), {("bill", 9)})]
    assert _classify(matches=wrong)[0] == "DISAGREE"
    two = [(_notif(1), {("bill", 5)}), (_notif(2), {("bill", 5)})]
    assert _classify(matches=two)[0] == "AMBIGUOUS"


def test_classify_unresolved_scope_fails_closed():
    assert proof.classify_row(reply_conversation_id="Conv-A", matches=[], expected=None,
                              project_id=None, work_date=None, pre_u579=False) == ("UNRESOLVED", "no_scope")


# ---- Exit code: all AGREE, except listed pre-U-579 NO_ROW ----
def test_exit_zero_only_when_all_agree_or_listed_pre_u579():
    assert proof.exit_code([("AGREE", "bound"), ("NO_ROW", "pre_u579_thread")]) == 0
    assert proof.exit_code([("AGREE", "bound"), ("SKIPPED", "out_of_scope")]) == 0


def test_exit_nonzero_for_any_other_no_row_or_failure():
    assert proof.exit_code([("NO_ROW", "no_row")]) == 1
    assert proof.exit_code([("NO_ROW", "null_conversation")]) == 1
    assert proof.exit_code([("DISAGREE", "wrong_document")]) == 1
    assert proof.exit_code([("AMBIGUOUS", "ambiguous")]) == 1
    assert proof.exit_code([("UNRESOLVED", "no_scope")]) == 1


def test_summarize_groups_by_label_and_reason():
    counts = proof.summarize([("AGREE", "bound"), ("NO_ROW", "pre_u579_thread"), ("NO_ROW", "no_row")])
    assert counts["AGREE"] == 1
    assert counts["NO_ROW"] == 2


def test_script_has_no_write_verbs_and_uses_the_shared_selector():
    text = _PATH.read_text(encoding="utf-8")
    body = text.upper()
    for verb in ("INSERT INTO", "UPDATE DBO.", "DELETE FROM", "MERGE INTO", "EXEC DBO.CREATE",
                 "EXEC DBO.ADD", "EXEC DBO.SET", "EXEC DBO.STAMP", "EXEC DBO.DELETE"):
        assert verb not in body, verb
    assert "select_notification" in text
    assert "--run" in text and "--report" in text


def test_report_ambiguity_rule_matches_the_backfill():
    rows = [
        SimpleNamespace(ClaimId=1, ProjectId=1, WorkDate="d", OutboxConversations=2, SentConversations=0),
        SimpleNamespace(ClaimId=2, ProjectId=1, WorkDate="d", OutboxConversations=0, SentConversations=2),
        SimpleNamespace(ClaimId=3, ProjectId=1, WorkDate="d", OutboxConversations=1, SentConversations=3),
        SimpleNamespace(ClaimId=4, ProjectId=1, WorkDate="d", OutboxConversations=0, SentConversations=1),
    ]
    assert [r.ClaimId for r in proof.ambiguous_cl_claims(rows)] == [1, 2]


# ---- Fix round 2, item 1 (N1): CL scope from TODAY's legacy resolver ----
def _resolver(**over):
    base = {"project_id": 1, "project_public_id": "P1", "work_date": date(2026, 11, 19),
            "match_kind": "conversation"}
    base.update(over)
    return base


def _cl_matches(project_id, work_date=date(2026, 11, 19), cl_id=7):
    n = _notif(1, kind="contract_labor", project_id=project_id, work_date=work_date)
    return [(n, {("contract_labor", cl_id)})]


_CL = {"cl_work_date": "2026-11-19", "cl_project_ids": {1, 2}}


def test_cl_multi_project_worker_is_agree_when_legacy_resolver_names_the_project():
    # One worker has lines on projects 1 and 2. The reply is on project 1's notification.
    got = proof.classify_cl_reply(
        reply_conversation_id="Conv-A", resolver_result=_resolver(),
        matches=_cl_matches(project_id=1), expected=("contract_labor", 7), pre_u579=False, **_CL)
    assert got == ("AGREE", "bound")


def test_cl_legacy_unresolved_with_no_notification_is_listed():
    # Both paths refuse: today's resolver cannot scope it and no notification row exists.
    for resolver in (None, _resolver(match_kind="fuzzy")):
        got = proof.classify_cl_reply(
            reply_conversation_id="Conv-A", resolver_result=resolver,
            matches=[], expected=("contract_labor", 7), pre_u579=False, **_CL)
        assert got == ("LISTED", "legacy_unresolved")


def test_cl_project_mismatch_is_disagree():
    got = proof.classify_cl_reply(
        reply_conversation_id="Conv-A", resolver_result=_resolver(project_id=2, project_public_id="P2"),
        matches=_cl_matches(project_id=1), expected=("contract_labor", 7), pre_u579=False, **_CL)
    assert got == ("DISAGREE", "wrong_project_or_date")


def test_listed_rows_do_not_fail_the_exit_code_and_are_counted():
    assert proof.exit_code([("AGREE", "bound"), ("LISTED", "legacy_unresolved")]) == 0
    assert proof.summarize([("LISTED", "legacy_unresolved")])["LISTED"] == 1

