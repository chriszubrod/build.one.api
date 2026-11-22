"""U-797c2b fix round 1 (F3): --run refuses to classify when the sproc does not return Subject,
and lists SUBJECT_FALLBACK rows. Pure fakes only: no database, Graph or HTTP.
"""
from __future__ import annotations

import contextlib
import importlib.util
from types import SimpleNamespace

import pytest

from tests.sproc_text import REPO_ROOT

_PATH = REPO_ROOT / "scripts" / "u797c1_rebind_proof.py"
_spec = importlib.util.spec_from_file_location("u797c1_rebind_proof_run_guard", _PATH)
proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proof)


def _notif_with_subject(nid, subject, *, kind="bill", project_id=None, work_date=None):
    return SimpleNamespace(Id=nid, Kind=kind, ProjectId=project_id, WorkDate=work_date, Subject=subject)


def _notif_without_subject(nid):
    # The shape of a row from a sproc that does NOT yet return Subject.
    return SimpleNamespace(Id=nid, Kind="bill", ProjectId=None, WorkDate=None)


def _row(**kw):
    base = dict(ReviewId=1, BillId=None, ExpenseId=None, ContractLaborId=None,
                CreatedDatetime=None, ConversationId="Conv-A", ClWorkDate=None,
                ClProjectIds=None, EmailSentDatetime=None, ReplySubject=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ---- the pure "every notification carries Subject" check -------------------------------
def test_missing_subject_check_reports_a_row_without_the_attribute():
    lookups = {"Conv-A": [(_notif_without_subject(7), {("bill", 5)})]}
    assert proof.missing_subject_notifications(lookups) == [("Conv-A", 7)]


def test_missing_subject_check_is_clean_when_every_row_has_the_attribute():
    lookups = {"Conv-A": [(_notif_with_subject(7, None), {("bill", 5)})]}
    assert proof.missing_subject_notifications(lookups) == []


# ---- _run wiring with fakes ------------------------------------------------------------
@pytest.fixture
def fake_run(monkeypatch):
    """Patch every seam _run touches. Returns a holder with the rows and lookup table."""
    holder = SimpleNamespace(rows=[], table={}, resolver=None)

    @contextlib.contextmanager
    def fake_connection():
        yield SimpleNamespace(cursor=lambda: None)

    import shared.database as sdb
    monkeypatch.setattr(sdb, "get_connection", fake_connection)
    monkeypatch.setattr(proof, "_fetch", lambda cursor, sql, params=(): holder.rows)

    from entities.review.business import review_notification_service as rns
    monkeypatch.setattr(rns.ReviewNotificationRecordService, "lookup_for_reply",
                        lambda self, conversation_id: list(holder.table.get(conversation_id, [])))

    from entities.contract_labor.business import service as cl_mod
    monkeypatch.setattr(cl_mod.ContractLaborService, "__init__", lambda self: None)
    monkeypatch.setattr(cl_mod.ContractLaborService, "find_for_reviewer_reply",
                        lambda self, conversation_id: holder.resolver)
    return holder


def test_run_aborts_with_nonzero_exit_before_classifying_when_subject_is_missing(fake_run, capsys):
    fake_run.rows = [_row(BillId=5)]
    fake_run.table = {"Conv-A": [(_notif_without_subject(7), {("bill", 5)})]}
    assert proof._run() == 2
    out = capsys.readouterr().out
    assert "Subject" in out
    assert "Replies checked" not in out, "nothing may be classified when the sproc lacks Subject"


def test_run_lists_a_bill_subject_fallback_as_a_failure(fake_run, capsys):
    fake_run.rows = [_row(BillId=5, ReplySubject="Retitled draft")]
    fake_run.table = {"Conv-A": [(_notif_with_subject(7, "Some other thread"), {("bill", 5)})]}
    assert proof._run() == 1
    out = capsys.readouterr().out
    assert "SUBJECT_FALLBACK" in out
    assert "legacy_unproven" in out


def test_run_lists_a_cl_subject_fallback_that_legacy_binds_and_passes(fake_run, capsys):
    fake_run.rows = [_row(ContractLaborId=7, ReplySubject="Retitled draft",
                          ClWorkDate="2026-11-19", ClProjectIds="1")]
    fake_run.table = {"Conv-A": [(_notif_with_subject(
        7, "Review: CL - Proj - 2026-11-19", kind="contract_labor", project_id=1,
        work_date="2026-11-19"), {("contract_labor", 7)})]}
    fake_run.resolver = {"match_kind": "conversation", "project_id": 1, "work_date": "2026-11-19"}
    assert proof._run() == 0
    out = capsys.readouterr().out
    assert "Listed for Chris" in out
    assert "legacy binds it" in out
