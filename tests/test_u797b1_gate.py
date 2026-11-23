"""U-797b1 date gate: the (project, date) crew email waits for suggestion rows, releases on all-terminal or
on the 30-minute timeout, and the snapshot it claims records exactly what the email showed. Stubs replace
the DB and outbox on the instance; the rows are the real dataclasses."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from unittest.mock import MagicMock

import pytest

from entities.contract_labor.persistence.coding_suggestion_repo import SuggestionRow
from entities.review.business import cl_notification_service as cl_ns
from entities.review.business import review_notification_service as rns

NOW = datetime.now(timezone.utc)
WORK_DATE = date(2026, 11, 19)


@dataclass(frozen=True)
class ContractLaborStub:
    public_id: str
    work_date: date


@dataclass(frozen=True)
class SubmittedLine:
    """Same columns as ReadSubmittedContractLaborLinesByWorkDate."""

    ContractLaborId: int
    EmployeeName: str
    ProjectId: int
    ProjectName: str
    ProjectAbbreviation: str
    LineItemId: int
    Hours: Optional[Decimal]
    IsBillable: Optional[bool]
    IsOverhead: Optional[bool]
    Description: Optional[str]


def _line(cl_id, name, line_id, hours="2.00"):
    return SubmittedLine(
        ContractLaborId=cl_id, EmployeeName=name, ProjectId=5, ProjectName="Oak Lane",
        ProjectAbbreviation="OL", LineItemId=line_id, Hours=Decimal(hours),
        IsBillable=True, IsOverhead=False, Description="Framing",
    )


def _row(cl_id, status="done", minutes_ago=5, **overrides):
    base = dict(
        id=cl_id * 10, contract_labor_id=cl_id, status=status, decision_source="history",
        sub_cost_code_id=100, sub_cost_code_number="65.02", sub_cost_code_name="Misc Labor",
        is_holding_code=False, may_split=False, model_split_code_ids=(),
        contract_labor_modified_datetime=NOW - timedelta(minutes=minutes_ago),
    )
    if status != "done":
        base.update(decision_source=None, sub_cost_code_id=None, sub_cost_code_number=None,
                    sub_cost_code_name=None)
    base.update(overrides)
    return SuggestionRow(**base)


def _svc(monkeypatch, *, suggestions_on=True, rows=(), labels=None, read_ok=True, pending=0, lines=None,
         claim_answers=None):
    svc = cl_ns.ContractLaborReviewNotificationService()
    lines = lines if lines is not None else [_line(1, "Ana", 100), _line(2, "Ben", 101)]
    reads = []
    claims = []
    review_claims = []
    sent = []
    answers = list(claim_answers) if claim_answers is not None else None

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda work_date: pending)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda work_date: lines)
    monkeypatch.setattr(svc, "_suggestions_in_review_enabled", lambda *a, **k: suggestions_on)
    monkeypatch.setattr(svc, "_load_settings", lambda: None)
    monkeypatch.setattr(svc, "_hold_repo", lambda: FakeHoldStore())

    def fake_read(project_id, work_date):
        reads.append((project_id, work_date))
        return (list(rows), dict(labels or {})) if read_ok else ([], {})

    monkeypatch.setattr(svc, "_read_suggestions", fake_read)

    def fake_claim(**kw):
        claims.append(kw)
        if answers is None:
            return True
        return answers.pop(0)

    monkeypatch.setattr(svc, "_claim_notification", fake_claim)

    def fake_review_claim(**kw):
        review_claims.append(kw)
        return None

    monkeypatch.setattr(svc, "_claim_review_notification", fake_review_claim)
    monkeypatch.setattr(svc, "_fetch_recipients", lambda cl_id: {})
    monkeypatch.setattr(svc, "_build_bcc_addresses", lambda: [])
    monkeypatch.setattr(cl_ns, "MsOutboxService", lambda: object())

    def fake_send(**kw):
        sent.append(kw)
        return object()

    monkeypatch.setattr(cl_ns.ReviewEmailComposer, "send_with_record", fake_send)
    return svc, reads, claims, review_claims, sent


def _run(svc):
    svc._do_enqueue(contract_labor=ContractLaborStub(public_id="cl-uuid", work_date=WORK_DATE))


# ---- the wait ---------------------------------------------------------------------------------

def test_pending_row_with_recent_submit_holds_the_email(monkeypatch):
    svc, reads, claims, review_claims, sent = _svc(
        monkeypatch, rows=[_row(1, status="pending", minutes_ago=5), _row(2, minutes_ago=5)])
    _run(svc)
    assert reads == [(5, WORK_DATE)]
    assert claims == [] and sent == [] and review_claims == []


def test_pending_row_past_thirty_minutes_releases_with_pending_note(monkeypatch):
    svc, _, claims, review_claims, sent = _svc(
        monkeypatch, rows=[_row(1, status="pending", minutes_ago=31), _row(2, minutes_ago=31)])
    _run(svc)
    assert len(sent) == 1
    body = sent[0]["mail"]["body"]
    assert "suggestion pending when sent" in body
    snaps = review_claims[0]["snapshots"]
    assert snaps[1].suggested_sub_cost_code_id is None
    assert snaps[1].suggestion_id is None
    assert snaps[1].suggestion_source is None
    assert snaps[1].suggestion_may_split is None


def test_all_terminal_rows_release_at_once(monkeypatch):
    svc, _, claims, _, sent = _svc(monkeypatch, rows=[_row(1), _row(2, status="failed")])
    _run(svc)
    assert len(sent) == 1
    body = sent[0]["mail"]["body"]
    assert "65.02 Misc Labor" in body
    assert "suggestion failed" in body


def test_flag_off_never_reads_and_never_waits(monkeypatch):
    svc, reads, _, _, sent = _svc(monkeypatch, suggestions_on=False, rows=[_row(1, status="pending")])
    _run(svc)
    assert reads == []
    assert len(sent) == 1
    assert "no suggestion" not in sent[0]["mail"]["body"]  # trunk's email: no table
    assert "Is Billable: Yes" in sent[0]["mail"]["body"]


def test_no_rows_with_flag_on_does_not_wait(monkeypatch):
    svc, reads, _, _, sent = _svc(monkeypatch, suggestions_on=True, rows=[])
    _run(svc)
    assert len(reads) == 1
    assert len(sent) == 1


def test_failed_read_releases_without_suggestions_and_is_logged_by_reader(monkeypatch):
    svc, _, _, _, sent = _svc(monkeypatch, suggestions_on=True, read_ok=False)
    _run(svc)
    assert len(sent) == 1
    assert "no suggestion" in sent[0]["mail"]["body"]


def test_date_gate_still_holds_before_any_suggestion_read(monkeypatch):
    svc, reads, claims, _, sent = _svc(monkeypatch, pending=1, rows=[_row(1)])
    _run(svc)
    assert reads == []
    assert claims == [] and sent == []


def test_double_fire_sends_once(monkeypatch):
    svc, _, claims, review_claims, sent = _svc(monkeypatch, rows=[_row(1), _row(2)],
                                              claim_answers=[True, False])
    _run(svc)
    _run(svc)
    assert len(claims) == 2
    assert len(sent) == 1
    assert len(review_claims) == 1


# ---- the snapshot ------------------------------------------------------------------------------

def test_clean_snapshot_records_what_the_email_showed(monkeypatch):
    svc, _, _, review_claims, sent = _svc(monkeypatch, rows=[_row(1, decision_source="model", id=77),
                                                             _row(2, id=78)])
    _run(svc)
    snaps = review_claims[0]["snapshots"]
    assert snaps[1] == rns.CrewTargetSnapshot(
        contract_labor_id=1, suggested_sub_cost_code_id=100, suggestion_id=77,
        suggestion_source="model", suggestion_may_split=False,
        lines_fingerprint=rns.cl_lines_fingerprint([(100, Decimal("2.00"))]),
    )


def test_split_snapshot_keeps_the_top_code_and_the_split_flag(monkeypatch):
    row = _row(1, may_split=True, model_split_code_ids=(3,), id=9)
    svc, _, _, review_claims, _ = _svc(monkeypatch, rows=[row, _row(2)], labels={3: ("3.00", "C")})
    _run(svc)
    snap = review_claims[0]["snapshots"][1]
    assert snap.suggested_sub_cost_code_id == 100
    assert snap.suggestion_id == 9
    assert snap.suggestion_may_split is True


def test_no_suggestion_snapshot_is_all_null(monkeypatch):
    row = _row(1, decision_source="none", sub_cost_code_id=None, sub_cost_code_number=None,
               sub_cost_code_name=None, id=5)
    svc, _, _, review_claims, _ = _svc(monkeypatch, rows=[row, _row(2)])
    _run(svc)
    snap = review_claims[0]["snapshots"][1]
    assert snap.suggested_sub_cost_code_id is None
    assert snap.suggestion_id is None
    assert snap.suggestion_source is None
    assert snap.suggestion_may_split is False


def test_holding_snapshot_is_null_code_with_split_false(monkeypatch):
    svc, _, _, review_claims, _ = _svc(monkeypatch, rows=[_row(1, is_holding_code=True), _row(2)])
    _run(svc)
    snap = review_claims[0]["snapshots"][1]
    assert snap.suggested_sub_cost_code_id is None
    assert snap.suggestion_may_split is False


def test_flag_off_writes_no_snapshot_at_all(monkeypatch):
    # F1: with the suggestions flag off the claim is trunk's: no snapshot is passed to the writer.
    svc, _, _, review_claims, _ = _svc(monkeypatch, suggestions_on=False)
    _run(svc)
    assert "snapshots" not in review_claims[0] or review_claims[0]["snapshots"] is None


def test_fingerprint_is_per_member_over_that_members_lines(monkeypatch):
    lines = [_line(1, "Ana", 100, hours="2.00"), _line(1, "Ana", 101, hours="1.00"),
             _line(2, "Ben", 102, hours="4.00")]
    svc, _, _, review_claims, _ = _svc(monkeypatch, suggestions_on=True, lines=lines)
    _run(svc)
    snaps = review_claims[0]["snapshots"]
    assert snaps[1].lines_fingerprint == rns.cl_lines_fingerprint(
        [(100, Decimal("2.00")), (101, Decimal("1.00"))])
    assert snaps[2].lines_fingerprint == rns.cl_lines_fingerprint([(102, Decimal("4.00"))])


def test_claim_still_carries_the_crew_ids_for_the_existing_writer(monkeypatch):
    svc, _, _, review_claims, _ = _svc(monkeypatch, suggestions_on=False)
    _run(svc)
    assert review_claims[0]["contract_labor_ids"] == [1, 2]


# ---- fix round 1 -------------------------------------------------------------------------------

def test_shadow_flag_alone_does_not_wait_or_read(monkeypatch):
    # F1: the shadow flag is already on in prod; the wait must ride only the new flag. _do_enqueue never asks it.
    svc, reads, _, review_claims, sent = _svc(monkeypatch, suggestions_on=False, rows=[_row(1, status="pending")])
    _run(svc)
    assert reads == [] and len(sent) == 1


class _Settings:
    def __init__(self, value):
        self.cl_suggestions_in_review_enabled = value


@pytest.mark.parametrize("value", [True, "true", "TRUE", " true "])
def test_flag_reads_true_only_for_real_truth(monkeypatch, value):
    monkeypatch.setattr("config.Settings", lambda: _Settings(value))
    assert cl_ns.ContractLaborReviewNotificationService()._suggestions_in_review_enabled(cl_ns.ContractLaborReviewNotificationService._load_settings()) is True


@pytest.mark.parametrize("value", [False, "false", "no", "1", None, 1, MagicMock()])
def test_flag_is_off_for_anything_that_is_not_true(monkeypatch, value):
    # F1: a MagicMock Settings (the test harnesses) must never turn the wait or the read on.
    monkeypatch.setattr("config.Settings", lambda: _Settings(value))
    assert cl_ns.ContractLaborReviewNotificationService()._suggestions_in_review_enabled(cl_ns.ContractLaborReviewNotificationService._load_settings()) is False


def test_flag_lookup_failure_is_off(monkeypatch):
    def boom():
        raise RuntimeError("settings down")

    monkeypatch.setattr("config.Settings", boom)
    assert cl_ns.ContractLaborReviewNotificationService()._suggestions_in_review_enabled(cl_ns.ContractLaborReviewNotificationService._load_settings()) is False


def test_submitted_only_hold_matches_the_sweep(monkeypatch):
    # F4: a pending row of a member who is not submitted must not hold the email.
    svc, reads, _, _, sent = _svc(
        monkeypatch, suggestions_on=True,
        rows=[
            SuggestionRow(id=10, contract_labor_id=1, status="pending", decision_source=None,
                          sub_cost_code_id=None, sub_cost_code_number=None, sub_cost_code_name=None,
                          is_holding_code=False, may_split=None, model_split_code_ids=(),
                          contract_labor_modified_datetime=NOW - timedelta(minutes=5),
                          contract_labor_status="ready"),
            _row(2, minutes_ago=5),
        ])
    _run(svc)
    assert len(sent) == 1


# ---- round 2 (H1, H4): the hold marker ---------------------------------------------------------

class FakeHoldStore:
    def __init__(self, fail_upsert=False):
        self.upserts = []
        self.deletes = []
        self.fail_upsert = fail_upsert

    def upsert_review_hold(self, project_id, work_date):
        if self.fail_upsert:
            raise RuntimeError("marker write down")
        self.upserts.append((project_id, work_date))

    def delete_review_hold(self, project_id, work_date):
        self.deletes.append((project_id, work_date))


def _hold_svc(monkeypatch, *, suggestions_on=True, rows=(), fail_upsert=False, claim_ok=True):
    svc, reads, claims, review_claims, sent = _svc(monkeypatch, suggestions_on=suggestions_on, rows=rows)
    store = FakeHoldStore(fail_upsert=fail_upsert)
    monkeypatch.setattr(svc, "_hold_repo", lambda: store)
    svc.holds = store
    if not claim_ok:
        monkeypatch.setattr(svc, "_claim_notification", lambda **kw: False)
    return svc, reads, claims, review_claims, sent, store


def test_marker_is_written_when_the_flag_is_on_and_the_day_is_held(monkeypatch):
    svc, _, claims, _, sent, store = _hold_svc(monkeypatch, rows=[_row(1, status="pending", minutes_ago=5)])
    _run(svc)
    assert store.upserts == [(5, WORK_DATE)]
    assert sent == [] and claims == []


def test_no_marker_when_the_flag_is_off(monkeypatch):
    svc, reads, _, _, sent, store = _hold_svc(monkeypatch, suggestions_on=False,
                                              rows=[_row(1, status="pending", minutes_ago=5)])
    _run(svc)
    assert store.upserts == [] and store.deletes == []
    assert reads == [] and len(sent) == 1


def test_marker_is_deleted_when_the_claim_succeeds(monkeypatch):
    svc, _, _, _, sent, store = _hold_svc(monkeypatch, rows=[_row(1), _row(2)])
    _run(svc)
    assert len(sent) == 1
    assert store.deletes == [(5, WORK_DATE)]
    assert store.upserts == []


def test_marker_is_kept_when_the_claim_is_refused(monkeypatch):
    svc, _, _, _, sent, store = _hold_svc(monkeypatch, rows=[_row(1), _row(2)], claim_ok=False)
    _run(svc)
    assert sent == [] and store.deletes == []


def test_flag_off_claim_success_touches_no_marker(monkeypatch):
    svc, _, _, _, sent, store = _hold_svc(monkeypatch, suggestions_on=False)
    _run(svc)
    assert len(sent) == 1
    assert store.deletes == [] and store.upserts == []


def test_a_failed_marker_write_sends_the_email_now_instead_of_stranding_it(monkeypatch):
    svc, _, _, _, sent, store = _hold_svc(monkeypatch, rows=[_row(1, status="pending", minutes_ago=5)],
                                          fail_upsert=True)
    _run(svc)
    assert len(sent) == 1


def test_marker_upsert_is_idempotent_per_held_day(monkeypatch):
    svc, _, _, _, sent, store = _hold_svc(monkeypatch, rows=[_row(1, status="pending", minutes_ago=5)])
    _run(svc)
    _run(svc)
    assert store.upserts == [(5, WORK_DATE), (5, WORK_DATE)]  # the store upserts; the repo is the idempotent part
    assert sent == []


def test_shadow_import_sits_under_the_flag_branch_in_do_enqueue():
    # H4: flag off must not import the coding shadow service while enqueueing.
    import ast
    from pathlib import Path

    src = Path(cl_ns.__file__).read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_do_enqueue")
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom) and node.module and "coding_shadow_service" in node.module:
            ancestors = [a for a in ast.walk(fn) if isinstance(a, ast.If) and any(
                child is node for child in ast.walk(a))]
            assert ancestors, "coding_shadow_service import must be inside an if"
            assert any("suggestions_on" in ast.unparse(a.test) for a in ancestors)
