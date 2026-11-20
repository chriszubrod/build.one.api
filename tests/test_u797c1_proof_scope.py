"""U-797c1 proof: the CL check is tied to the applied record (review N1-N3, EM 2026-11-20)."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from scripts import u797c1_rebind_proof as proof


def _resolver(project_id=1, work_date=date(2026, 11, 19)):
    return {"project_id": project_id, "project_public_id": f"P{project_id}", "work_date": work_date,
            "match_kind": "conversation"}


def _matches(project_id=1, work_date=date(2026, 11, 19)):
    n = SimpleNamespace(Kind="contract_labor", ProjectId=project_id, WorkDate=work_date)
    return [(n, {("contract_labor", 7)})]


def _cl(reply="Conv-A", resolver=None, matches=None, cl_work_date="2026-11-19", cl_project_ids=(1,)):
    return proof.classify_cl_reply(
        reply_conversation_id=reply, resolver_result=resolver, matches=matches if matches is not None else [],
        expected=("contract_labor", 7), pre_u579=False,
        cl_work_date=cl_work_date, cl_project_ids=set(cl_project_ids))


def test_n1_applied_record_on_another_date_is_disagree():
    got = _cl(resolver=_resolver(), matches=_matches(), cl_work_date="2026-11-18")
    assert got == ("DISAGREE", "applied_cl_outside_scope")


def test_n1_applied_record_without_a_line_on_the_scope_project_is_disagree():
    got = _cl(resolver=_resolver(project_id=1), matches=_matches(project_id=1), cl_project_ids=(2,))
    assert got == ("DISAGREE", "applied_cl_outside_scope")


def test_n1_applied_record_inside_scope_still_agrees():
    assert _cl(resolver=_resolver(), matches=_matches()) == ("AGREE", "bound")


def test_n2_legacy_unresolved_but_new_binder_would_bind_is_new_bind_and_fails():
    got = _cl(resolver=None, matches=_matches(project_id=1))
    assert got == ("NEW_BIND", "legacy_refused")
    assert proof.exit_code([got]) == 1


def test_n2_legacy_unresolved_and_new_binder_refuses_fails():
    # A row exists but for another project: the new binder would REFUSE an applied reply (no
    # fallback), which D5 forbids, so this fails rather than being listed.
    got = _cl(resolver=None, matches=_matches(project_id=9), cl_project_ids=(1,))
    assert got == ("DISAGREE", "legacy_unresolved_binder_refuses")
    assert proof.exit_code([got]) == 1


def test_n2_legacy_unresolved_and_ambiguous_rows_fail():
    two = _matches(project_id=1) + _matches(project_id=1)
    got = _cl(resolver=None, matches=two)
    assert got == ("AMBIGUOUS", "legacy_unresolved_binder_refuses")
    assert proof.exit_code([got]) == 1


def test_n2_legacy_unresolved_with_no_row_is_listed():
    # Only "no notification row at all" is truly both-paths-refuse.
    assert _cl(resolver=None, matches=[]) == ("LISTED", "legacy_unresolved")


@pytest.mark.parametrize("cl_work_date, cl_project_ids", [(None, (1,)), ("2026-11-19", ())])
def test_unscoped_record_missing_date_or_projects_is_unresolved_and_fails(cl_work_date, cl_project_ids):
    got = _cl(resolver=None, matches=_matches(), cl_work_date=cl_work_date, cl_project_ids=cl_project_ids)
    assert got == ("UNRESOLVED", "applied_cl_missing")
    assert proof.exit_code([got]) == 1


def test_n3_cl_reply_with_no_conversation_is_null_conversation_and_fails():
    got = _cl(reply=None, resolver=None, matches=[])
    assert got == ("NO_ROW", "null_conversation")
    assert proof.exit_code([got]) == 1


def test_sweep_selects_the_applied_record_date_and_projects():
    assert "AS ClWorkDate" in proof.SWEEP_SQL
    assert "AS ClProjectIds" in proof.SWEEP_SQL


def test_new_bind_is_a_reported_label():
    assert "NEW_BIND" in proof.LABELS
