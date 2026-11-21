"""U-797c1a: pre_u579 from email send time, post-approval edits, and LEGACY_BIND in the proof."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from scripts import u797c1_rebind_proof as proof


def _resolver(project_id=1, work_date=date(2026, 11, 19)):
    return {"project_id": project_id, "project_public_id": f"P{project_id}", "work_date": work_date,
            "match_kind": "conversation"}


def _matches(project_id=1, work_date=date(2026, 11, 19)):
    n = SimpleNamespace(Kind="contract_labor", ProjectId=project_id, WorkDate=work_date)
    return [(n, {("contract_labor", 7)})]


def _cl(*, resolver=None, matches=None, cl_work_date="2026-11-19", cl_project_ids=(1,),
        pre=False, reply="Conv-A"):
    return proof.classify_cl_reply(
        reply_conversation_id=reply, resolver_result=resolver,
        matches=matches if matches is not None else [],
        expected=("contract_labor", 7), pre_u579=pre,
        cl_work_date=cl_work_date, cl_project_ids=set(cl_project_ids))


# ---- 1. pre_u579 decided by when the review EMAIL was sent, not the Review's CreatedDatetime ----
def test_email_sent_before_cutoff_is_pre_u579():
    assert proof.email_predates_u579(datetime(2026, 9, 29, 23, 59)) is True


def test_email_sent_at_or_after_cutoff_is_not_pre_u579():
    assert proof.email_predates_u579(datetime(2026, 9, 30, 0, 0)) is False
    assert proof.email_predates_u579(datetime(2026, 10, 2, 9, 0)) is False


def test_no_send_found_is_not_pre_u579():
    assert proof.email_predates_u579(None) is False


def test_pre_u579_send_with_reply_after_cutoff_passes_as_pre_u579_no_row():
    # Cause (a): the Review was created after the cutoff, but its email went out before it.
    pre = proof.email_predates_u579(datetime(2026, 9, 29, 10, 0))
    got = proof.classify_row(reply_conversation_id="Conv-A", matches=[], expected=("bill", 5),
                             project_id=None, work_date=None, pre_u579=pre)
    assert got == ("NO_ROW", "pre_u579_thread")
    assert proof.exit_code([got]) == 0


def test_post_cutoff_send_with_no_row_fails():
    pre = proof.email_predates_u579(datetime(2026, 10, 2, 10, 0))
    got = proof.classify_row(reply_conversation_id="Conv-A", matches=[], expected=("bill", 5),
                             project_id=None, work_date=None, pre_u579=pre)
    assert got == ("NO_ROW", "no_row")
    assert proof.exit_code([got]) == 1


def test_sweep_sql_takes_the_email_send_time_from_the_done_outbox_row_at_or_before_the_review():
    sql = proof.SWEEP_SQL
    assert "AS EmailSentDatetime" in sql
    assert "o.[Kind] = N'send_mail'" in sql
    assert "o.[Status] = N'done'" in sql
    assert "o.[CreatedDatetime] <= r.[CreatedDatetime]" in sql
    assert "o.[EntityPublicId]" in sql or "o.[EntityPublicId] =" in sql


# ---- 2. applied record moved off the scope later: pass ONLY when the new binder agrees with legacy ----
# Line ModifiedDatetime is not evidence (every CL approval has later line writes, 185/185 on prod),
# and nothing records project history. What matters for the flag flip is whether the new binder
# lands on the same (project, date) as today's legacy resolver.
def test_record_outside_scope_but_binder_agrees_with_legacy_is_listed_and_passes():
    # Legacy scopes project 1 / 11-19; the record for that conversation is project 1 / 11-19;
    # the applied CL's lines were later moved to project 2.
    got = _cl(resolver=_resolver(project_id=1), matches=_matches(project_id=1), cl_project_ids=(2,))
    assert got == ("LISTED", "scope_agrees_record_changed")
    assert proof.exit_code([got]) == 0


def test_record_outside_scope_with_no_row_agrees_with_legacy_and_passes():
    got = _cl(resolver=_resolver(project_id=1), matches=[], cl_project_ids=(2,))
    assert got == ("LISTED", "scope_agrees_record_changed")


def test_record_outside_scope_and_binder_disagrees_with_legacy_fails():
    # The record for the conversation is project 9 but legacy scopes project 1: a real disagreement.
    got = _cl(resolver=_resolver(project_id=1), matches=_matches(project_id=9), cl_project_ids=(2,))
    assert got == ("DISAGREE", "applied_cl_outside_scope")
    assert proof.exit_code([got]) == 1


def test_record_outside_scope_and_ambiguous_fails():
    two = _matches(project_id=1) + _matches(project_id=1)
    got = _cl(resolver=_resolver(project_id=1), matches=two, cl_project_ids=(2,))
    assert got == ("AMBIGUOUS", "ambiguous")
    assert proof.exit_code([got]) == 1


def test_wrong_project_or_date_inside_scope_still_fails():
    # Applied CL is inside the legacy scope but the record says another project: DISAGREE stands.
    got = _cl(resolver=_resolver(project_id=1), matches=_matches(project_id=2))
    assert got == ("DISAGREE", "wrong_project_or_date")
    assert proof.exit_code([got]) == 1


def test_sweep_sql_no_longer_uses_line_modified_time_as_evidence():
    assert "ClLinesEditedAfterApply" not in proof.SWEEP_SQL
    assert "post_approval_edit" not in open(proof.__file__).read()


# ---- 3. CL NO_ROW inside the legacy scope: the flag-on path falls back to legacy, which binds ----
def test_cl_no_row_inside_the_legacy_scope_is_legacy_bind_and_passes():
    got = _cl(resolver=_resolver(project_id=1), matches=[], cl_project_ids=(1,))
    assert got == ("LEGACY_BIND", "legacy_binds")
    assert proof.exit_code([got]) == 0


def test_cl_no_row_inside_scope_is_legacy_bind_even_after_the_u579_cutoff():
    # Not a pre-U-579 thread, so it is not the NO_ROW pre_u579 pass; LEGACY_BIND is what passes it.
    got = _cl(resolver=_resolver(project_id=1), matches=[], cl_project_ids=(1,), pre=False)
    assert got == ("LEGACY_BIND", "legacy_binds")


def test_cl_bound_inside_scope_is_still_agree_not_legacy_bind():
    assert _cl(resolver=_resolver(project_id=1), matches=_matches(project_id=1)) == ("AGREE", "bound")


def test_legacy_bind_is_a_reported_label_and_is_counted():
    assert "LEGACY_BIND" in proof.LABELS
    assert proof.summarize([("LEGACY_BIND", "legacy_binds")])["LEGACY_BIND"] == 1


# ---- 4. exit code: pass the new labels, still fail everything else ----
def test_exit_code_passes_legacy_bind_and_listed_scope_agreement():
    assert proof.exit_code([("LEGACY_BIND", "legacy_binds"), ("LISTED", "scope_agrees_record_changed")]) == 0


def test_exit_code_still_fails_disagree_ambiguous_new_bind_no_row_and_null_conversation():
    for result in (("DISAGREE", "applied_cl_outside_scope"), ("DISAGREE", "wrong_project_or_date"),
                   ("AMBIGUOUS", "ambiguous"), ("NEW_BIND", "legacy_refused"),
                   ("NO_ROW", "no_row"), ("NO_ROW", "null_conversation")):
        assert proof.exit_code([result]) == 1, result
