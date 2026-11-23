"""U-797b1 tick (round 2). The sweep and the completion re-fire act ONLY on marked (project, date) holds
(dbo.ContractLaborReviewHold). The sweep runs on every tick whatever the flags, and prunes markers whose day
already has a claim. A fire that does not delete its marker counts one attempt; the fifth gives up and logs
ids only. A day with no marker is never fired, so a deleted pending_review day or a failed send is not drafted."""

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from entities.contract_labor.business import coding_shadow_service as css
from entities.contract_labor.business.coding_shadow import ShadowInputs
from entities.contract_labor.persistence.coding_suggestion_repo import (
    ClaimedSuggestion,
    ReviewHold,
    ReviewHoldFire,
    SuggestionRow,
)

WORK = date(2026, 11, 19)
NOW = datetime.now(timezone.utc)
LOGGER = "entities.contract_labor.business.coding_shadow_service"


@dataclass(frozen=True)
class Settings:
    cl_coding_shadow_enabled: bool = True
    cl_coding_shadow_daily_call_cap: int = 60


async def _not_stopped():
    return False


async def _headroom():
    return True


def _claim(sid, cl_id, attempts=0, project=5):
    return ClaimedSuggestion(id=sid, contract_labor_id=cl_id, project_id=project, work_date=WORK,
                             vendor_id=None, attempt_count=attempts)


def _sibling(sid, cl_id, status="done", project=5, pending_line=True):
    return SuggestionRow(
        id=sid, contract_labor_id=cl_id, status=status, decision_source=None, sub_cost_code_id=None,
        sub_cost_code_number=None, sub_cost_code_name=None, is_holding_code=False, may_split=None,
        model_split_code_ids=(), contract_labor_modified_datetime=NOW, has_submitted_project_line=pending_line,
    )


def _hold(hid, cl_id=10, project=5, minutes_held=0, attempts=0, gave_up=None, work=WORK):
    return ReviewHold(id=hid, project_id=project, work_date=work,
                      held_datetime=NOW - timedelta(minutes=minutes_held), fire_attempts=attempts,
                      gave_up_datetime=gave_up, contract_labor_id=cl_id)


@dataclass
class FakeRepo:
    claims: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    holds: list = field(default_factory=list)
    own_status: str = "approved"
    read_raises: bool = False
    claim_raises: bool = False
    completed: list = field(default_factory=list)
    released: list = field(default_factory=list)
    prunes: int = 0
    marker_reads: list = field(default_factory=list)
    pending_review: int = 0
    pending_raises: bool = False

    def claim_next(self):
        if self.claim_raises:
            raise RuntimeError("claim broke")
        return self.claims.pop(0) if self.claims else None

    def read_inputs(self, contract_labor_id, project_id):
        if self.read_raises:
            raise RuntimeError("boom")
        return ShadowInputs(total_hours=Decimal("8"), day_note_text=None, precedent_days=(), catalog=(),
                            own_status=self.own_status)

    def complete(self, suggestion_id, **fields):
        self.completed.append((suggestion_id, fields))

    def release(self, suggestion_id, count_attempt=False):
        self.released.append((suggestion_id, count_attempt))

    def count_calls_today(self):
        return 0

    def read_suggestions_for_project_day(self, project_id, work_date):
        return list(self.rows), {}

    def prune_review_holds(self):
        self.prunes += 1

    def count_pending_review(self, work_date):
        if self.pending_raises:
            raise RuntimeError("count broke")
        return self.pending_review

    def read_review_holds_due(self, days=7):
        return [h for h in self.holds if h.gave_up_datetime is None and h.fire_attempts < 5]

    def read_review_hold(self, project_id, work_date):
        self.marker_reads.append((project_id, work_date))
        for h in self.holds:
            if h.project_id == project_id and h.work_date == work_date:
                return h
        return None

    def record_review_hold_fire(self, hold_id, max_attempts=5):
        for i, h in enumerate(self.holds):
            if h.id == hold_id:
                attempts = h.fire_attempts + 1
                gave = NOW if attempts >= max_attempts else h.gave_up_datetime
                self.holds[i] = replace(h, fire_attempts=attempts, gave_up_datetime=gave)
                return ReviewHoldFire(id=hold_id, project_id=h.project_id, work_date=h.work_date,
                                      fire_attempts=attempts, gave_up_datetime=gave)
        return None  # the marker is gone: the fire deleted it


def _tick(repo, *, settings=None, refire=None):
    fired = []

    def default_fire(cl_id):
        fired.append(cl_id)

    summary = css.run_cl_coding_tick_sync(
        limit=1,
        settings=settings if settings is not None else Settings(),
        repo=repo,
        line_item_repo=object(),
        budget_stopped=_not_stopped,
        spend_headroom=_headroom,
        model_call=None,
        refire=refire if refire is not None else default_fire,
    )
    return summary, fired


# ---- the sweep: markers only, every tick, whatever the flags -----------------------------------

def test_flag_off_takes_no_claims_but_sweeps_a_marked_hold_and_prunes():
    repo = FakeRepo(claims=[_claim(1, 10)], holds=[_hold(1)])
    summary, fired = _tick(repo, settings=Settings(cl_coding_shadow_enabled=False))
    assert summary["skipped"] == "disabled" and summary["claimed"] == 0
    assert repo.completed == [] and repo.released == []
    assert fired == [10]
    assert repo.prunes == 1
    assert repo.holds[0].fire_attempts == 1


def test_unmarked_days_are_never_fired():
    # A deleted pending_review day and a failed send with no marker: nothing in the hold table, so no fire.
    repo = FakeRepo(holds=[])
    summary, fired = _tick(repo)
    assert fired == []
    assert summary["swept"] == 0


def test_a_marker_is_due_once_its_hold_is_older_than_the_wait():
    repo = FakeRepo(rows=[_sibling(1, 10, status="pending")], holds=[_hold(1, minutes_held=31)])
    _, fired = _tick(repo)
    assert fired == [10]


def test_a_marker_is_not_due_while_the_hold_is_young_and_a_member_is_pending():
    repo = FakeRepo(rows=[_sibling(1, 10, status="pending")], holds=[_hold(1, minutes_held=5)])
    summary, fired = _tick(repo)
    assert fired == []
    assert repo.holds[0].fire_attempts == 0
    assert summary["swept"] == 0


def test_a_marker_is_due_once_the_crew_has_settled_even_if_young():
    repo = FakeRepo(rows=[_sibling(1, 10, status="done")], holds=[_hold(1, minutes_held=5)])
    _, fired = _tick(repo)
    assert fired == [10]


def test_a_pending_member_without_a_project_line_does_not_keep_a_marker_young():
    repo = FakeRepo(rows=[_sibling(1, 10, status="done"), _sibling(2, 11, status="pending", pending_line=False)],
                    holds=[_hold(1, minutes_held=5)])
    _, fired = _tick(repo)
    assert fired == [10]


def test_a_marker_without_a_representative_crew_member_is_skipped():
    repo = FakeRepo(holds=[_hold(1, cl_id=None, minutes_held=40)])
    summary, fired = _tick(repo)
    assert fired == []
    assert repo.holds[0].fire_attempts == 0


def test_one_fire_per_work_date_records_each_marker_on_that_date():
    other = _hold(2, cl_id=20, project=6, minutes_held=40)
    repo = FakeRepo(holds=[_hold(1, minutes_held=40), other])
    summary, fired = _tick(repo)
    assert fired == [10]
    assert [h.fire_attempts for h in repo.holds] == [1, 1]
    assert summary["swept"] == 1


def test_a_fire_that_deletes_its_marker_records_no_attempt():
    repo = FakeRepo(holds=[_hold(1, minutes_held=40)])

    def deleting_fire(cl_id):
        repo.holds.clear()

    summary, _ = _tick(repo, refire=deleting_fire)
    assert repo.holds == []
    assert summary["swept"] == 1


def test_a_failed_send_still_counts_an_attempt():
    repo = FakeRepo(holds=[_hold(1, minutes_held=40)])

    def failing_fire(cl_id):
        raise RuntimeError("send failed")

    summary, _ = _tick(repo, refire=failing_fire)
    assert repo.holds[0].fire_attempts == 1
    assert summary["swept"] == 0


def test_five_attempts_give_up_stop_firing_and_log_ids_only(caplog):
    repo = FakeRepo(holds=[_hold(7, project=42, minutes_held=40)])
    fires = []

    def never_deletes(cl_id):
        fires.append(cl_id)

    caplog.set_level(logging.ERROR, logger=LOGGER)
    for _ in range(7):
        _tick(repo, refire=never_deletes)
    assert len(fires) == 5
    assert repo.holds[0].gave_up_datetime is not None
    gave_up = [r for r in caplog.records if "gave_up" in r.getMessage()]
    assert len(gave_up) == 1
    message = gave_up[0].getMessage()
    assert "hold_id=7" in message and "project_id=42" in message
    assert "2026-11-19" not in message  # ids only


def test_a_claim_next_error_does_not_skip_the_sweep():
    repo = FakeRepo(claims=[_claim(1, 10)], claim_raises=True, holds=[_hold(1, cl_id=12, minutes_held=40)])
    summary, fired = _tick(repo)
    assert fired == [12]


def test_a_refire_error_on_a_settled_row_still_sweeps():
    # The completion refire raises on WORK; the sweep still runs, for a marked day on another work date.
    other_day = date(2026, 11, 20)
    repo = FakeRepo(claims=[_claim(1, 10)], rows=[_sibling(1, 10)],
                    holds=[_hold(1, cl_id=10, minutes_held=5), _hold(2, cl_id=13, minutes_held=40, work=other_day)])
    fired = []

    def boom(cl_id):
        fired.append(cl_id)
        raise RuntimeError("drafts down")

    summary, _ = _tick(repo, refire=boom)
    assert fired == [10, 13]  # the completion refire (raised), then the sweep on the other date (raised)
    assert summary["completed"] == 1


# ---- the completion re-fire: marked days only ---------------------------------------------------

def test_a_settled_crew_day_without_a_marker_is_not_refired():
    repo = FakeRepo(claims=[_claim(1, 10)], rows=[_sibling(1, 10), _sibling(2, 11)], holds=[])
    summary, fired = _tick(repo)
    assert repo.completed[0][1]["status"] == "skipped"
    assert fired == []
    assert summary["refired"] == 0


def test_a_settled_crew_day_with_a_marker_is_refired_and_the_attempt_recorded():
    repo = FakeRepo(claims=[_claim(1, 10)], rows=[_sibling(1, 10), _sibling(2, 11)],
                    holds=[_hold(1, minutes_held=5)])
    summary, fired = _tick(repo)
    assert fired == [10]
    assert summary["refired"] == 1
    assert repo.holds[0].fire_attempts == 1


def test_a_sibling_still_pending_blocks_the_completion_refire():
    repo = FakeRepo(claims=[_claim(1, 10)], rows=[_sibling(1, 10), _sibling(2, 11, status="pending")],
                    holds=[_hold(1, minutes_held=5)])
    _, fired = _tick(repo)
    assert fired == []


def test_a_gave_up_marker_is_not_refired_on_completion():
    repo = FakeRepo(claims=[_claim(1, 10)], rows=[_sibling(1, 10)],
                    holds=[_hold(1, minutes_held=5, attempts=5, gave_up=NOW)])
    _, fired = _tick(repo)
    assert fired == []


def test_first_failed_attempt_releases_and_does_not_refire():
    # The row is released, so no completion re-fire. The settled marked day is then due for the sweep: one fire.
    repo = FakeRepo(claims=[_claim(1, 10, attempts=0)], read_raises=True, rows=[_sibling(1, 10)],
                    holds=[_hold(1, minutes_held=5)])
    summary, fired = _tick(repo)
    assert repo.released == [(1, True)]
    assert fired == [10]
    assert summary["refired"] == 0 and summary["swept"] == 1


def test_third_strike_completes_failed_and_refires_only_a_marked_day():
    repo = FakeRepo(claims=[_claim(1, 10, attempts=2)], read_raises=True,
                    rows=[_sibling(1, 10, "failed"), _sibling(2, 11)], holds=[_hold(1, minutes_held=5)])
    _, fired = _tick(repo)
    assert repo.completed[0][1]["status"] == "failed"
    assert fired == [10]


def test_model_unavailable_third_strike_refires_a_marked_day(monkeypatch):
    async def unavailable(claim, **kwargs):
        return "unavailable"

    monkeypatch.setattr(css, "_process_claim", unavailable)
    repo = FakeRepo(claims=[_claim(1, 10, attempts=2)], rows=[_sibling(1, 10, "failed"), _sibling(2, 11)],
                    holds=[_hold(1, minutes_held=5)])
    summary, fired = _tick(repo)
    assert repo.completed[0][1]["failure_reason"] == "model_unavailable"
    assert fired == [10]
    assert summary["refired"] == 1


def test_model_unavailable_third_strike_without_a_marker_does_not_refire(monkeypatch):
    async def unavailable(claim, **kwargs):
        return "unavailable"

    monkeypatch.setattr(css, "_process_claim", unavailable)
    repo = FakeRepo(claims=[_claim(1, 10, attempts=2)], rows=[_sibling(1, 10, "failed")], holds=[])
    _, fired = _tick(repo)
    assert repo.completed and fired == []


def test_real_default_refire_calls_enqueue_drafts_for_the_contract_labor(monkeypatch):
    from entities.contract_labor.business import service as cl_service_mod
    from entities.review.business import cl_notification_service as cl_ns

    cl_obj = object()
    calls = []

    class _Repo:
        def read_by_id(self, cl_id):
            calls.append(("read", cl_id))
            return cl_obj if cl_id == 10 else None

    class _Service:
        repo = _Repo()

    class _Drafts:
        def enqueue_drafts(self, *, contract_labor):
            calls.append(("drafts", contract_labor))

    monkeypatch.setattr(cl_service_mod, "ContractLaborService", lambda: _Service())
    monkeypatch.setattr(cl_ns, "ContractLaborReviewNotificationService", lambda: _Drafts())
    css._fire_crew_drafts(10)
    css._fire_crew_drafts(99)
    assert calls == [("read", 10), ("drafts", cl_obj), ("read", 99)]


# --- round 3 (EM): a marker is not due, and no attempt is counted, while the date still waits on a submit ---

def test_marker_not_due_while_a_member_is_pending_review():
    fired = []
    repo = FakeRepo(holds=[_hold(1, minutes_held=45)], pending_review=1)
    out = _tick(repo, refire=fired.append)
    assert fired == []
    assert repo.holds[0].fire_attempts == 0  # no attempt burned while the date gate waits


def test_marker_not_due_when_the_pending_count_cannot_be_read():
    fired = []
    repo = FakeRepo(holds=[_hold(1, minutes_held=45)], pending_raises=True)
    _tick(repo, refire=fired.append)
    assert fired == [] and repo.holds[0].fire_attempts == 0


def test_completion_refire_waits_while_a_member_is_pending_review():
    fired = []
    repo = FakeRepo(claims=[_claim(1, 10)], rows=[_sibling(1, 10)], holds=[_hold(1)], pending_review=2)
    _tick(repo, refire=fired.append)
    assert fired == [] and repo.holds[0].fire_attempts == 0
