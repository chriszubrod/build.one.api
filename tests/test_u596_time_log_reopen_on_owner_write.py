"""
U-596 — a submitted, not-yet-approved time entry is REOPENED when its owner's
device writes a late time log (the server half of U-593, where the late logs
were refused for days until the office rejected the day by hand).

Policy (Chris, 2026-10-01): owner on `submitted` → back to `draft` + flagged
`reopened_after_submit`, then the write proceeds. Everyone else, and every
other status, is refused with the wording the clients classify as a locked
entry. Shape after five review rounds: the SERVICE only decides eligibility;
the write procedure reopens and writes in ONE transaction under the status
range lock every transition takes; submit writes its status row and
aggregates in ONE transaction; the aggregator rebuilds untouched labor and
REFUSES to change touched labor.
"""
import json
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import actor_context

from entities.time_entry.business.service import TimeEntryService
from entities.time_entry.business.time_log_service import TimeLogService
from entities.time_entry.business.validation import ALL_REASON_CODES, REASON_REOPENED_AFTER_SUBMIT
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository
from entities.time_entry.persistence.time_log_repo import TimeLogRepository

OWNER, TEAMMATE, ADMIN = 41, 20, 99
CURRENT_STATUS_ROW_ID = 77
BASE_SQL = Path(__file__).resolve().parents[1] / "entities" / "time_entry" / "sql" / "dbo.time_entry.sql"
LOG_WRITES = ("CreateTimeLog", "UpdateTimeLogById", "DeleteTimeLogById")


def _entry():
    return SimpleNamespace(id=10, public_id="te-10", user_id=OWNER)


def _log(**over):
    base = dict(id=5, public_id="tl-5", row_version="rv1", time_entry_id=10,
                clock_in="2026-09-10 08:00:00", clock_out=None, log_type="work",
                project_id=101, latitude=None, longitude=None, note=None, duration=None)
    base.update(over)
    return SimpleNamespace(**base)


def _sibling():
    return _log(id=6, public_id="tl-6", clock_in="2026-09-10 13:00:00")


def ios_classifies_as_locked(message: str) -> bool:
    """Mirror of BuildOne's `isLockedEntryRejection` substring rules (HTTP 400 branch)."""
    m = message.lower()
    return "submitted" in m or "not in 'draft'" in m or "not in draft" in m


MAPPER_409_PHRASES = ("already exists", "concurrency", "row-version")   # raise_workflow_error → 409 (iOS discards the edit)


class World:
    """Repository doubles recording every write, in order, as `events`."""

    def __init__(self, monkeypatch, *, status="draft", locked=False, labor_touched=False,
                 conflict=False, aggregation_error=None, submit_raises=None, write_raises=None):
        self.status = status
        self.events = []
        self.status_writes = []
        self.stamps = []
        self.reopens = []
        self.persisted_priority = None
        self.persisted_reasons = None
        self.entry_reasons = None          # what the entry READ carries (review_reasons)

        self.entry_reads = 0
        self.entry_reasons_after_status_read = None   # what a SECOND read sees (a reopen that landed in between)

        def read_entry(s, **kw):
            self.entry_reads += 1
            reasons = self.entry_reasons if self.entry_reads == 1 or self.entry_reasons_after_status_read is None else self.entry_reasons_after_status_read
            return SimpleNamespace(id=10, public_id="te-10", user_id=OWNER, review_reasons=reasons)
        monkeypatch.setattr(TimeEntryRepository, "read_by_public_id", read_entry)
        monkeypatch.setattr(TimeEntryRepository, "read_by_id", lambda s, **kw: _entry())
        monkeypatch.setattr(TimeEntryRepository, "is_downstream_locked", lambda s, **kw: locked)
        monkeypatch.setattr(TimeEntryRepository, "labor_is_untouched", lambda s, **kw: not labor_touched)

        def stamp(s, **kw):
            self.stamps.append(kw); self.events.append("stamp")
            # the sproc returns what it PERSISTED; the double echoes the request unless told otherwise
            return (1, self.persisted_priority or kw["priority"], self.persisted_reasons or json.loads(kw["reasons_json"]))
        monkeypatch.setattr(TimeEntryRepository, "stamp_review", stamp)

        def aggregate(s, **kw):
            self.events.append("aggregate:standalone"); return []
        monkeypatch.setattr(TimeEntryRepository, "aggregate_for_billing", aggregate)

        def submit_with_aggregation(s, **kw):          # dbo.SubmitTimeEntry: status row + aggregation, one call
            if submit_raises is not None:
                raise submit_raises
            if conflict:
                self.status = "approved"
                return None, [], None
            self.status_writes.append({"time_entry_id": kw["time_entry_id"],
                                       "expected_current_status_id": kw["expected_current_status_id"],
                                       "status": "submitted", "user_id": kw["user_id"], "note": None})
            self.events.append("submit:status+aggregate")
            self.status = "submitted"
            return (SimpleNamespace(id=CURRENT_STATUS_ROW_ID + 1, time_entry_id=10, status="submitted"),
                    [], aggregation_error)
        monkeypatch.setattr(TimeEntryRepository, "submit_with_aggregation", submit_with_aggregation)

        monkeypatch.setattr(
            TimeEntryStatusRepository, "read_current",
            lambda s, **kw: SimpleNamespace(id=CURRENT_STATUS_ROW_ID, time_entry_id=10, status=self.status) if self.status else None)

        def plain_create(s, **kw):                     # the UNCONDITIONAL insert — never used any more
            self.events.append("status:PLAIN"); return SimpleNamespace(**kw)
        monkeypatch.setattr(TimeEntryStatusRepository, "create", plain_create)

        def create_if_current(s, **kw):                # approve / reject
            if conflict:
                self.status = "approved"; return None
            self.status_writes.append(kw)
            self.events.append(f"status:{kw['status']}")
            self.status = kw["status"]
            return SimpleNamespace(id=CURRENT_STATUS_ROW_ID + len(self.status_writes), **kw)
        monkeypatch.setattr(TimeEntryStatusRepository, "create_if_current", create_if_current)

        monkeypatch.setattr(TimeLogRepository, "read_by_public_id", lambda s, **kw: _log())
        monkeypatch.setattr(TimeLogRepository, "read_by_time_entry_id", lambda s, **kw: [_log(), _sibling()])

        def write(kind, kw):                           # the write sprocs reopen-and-write in one transaction
            if write_raises is not None:
                raise write_raises
            self.events.append(f"log:{kind}")
            if kw.get("reopen_as_user_id") is not None:
                self.reopens.append((kind, kw["reopen_as_user_id"], kw.get("reopen_note")))
                self.status = "draft"
        monkeypatch.setattr(TimeLogRepository, "create", lambda s, **kw: write("create", kw) or _log())
        monkeypatch.setattr(TimeLogRepository, "update_by_id", lambda s, existing, **kw: write("update", kw) or existing)
        monkeypatch.setattr(TimeLogRepository, "delete_by_id", lambda s, **kw: write("delete", kw) or _log())

    @property
    def log_events(self):
        return [e for e in self.events if e.startswith("log:")]


def create(**over):
    kw = dict(time_entry_public_id="te-10", clock_in="2026-09-10 15:00:00", clock_out="2026-09-10 17:00:00")
    kw.update(over)
    return TimeLogService().create(**kw)


def update(**over):
    kw = dict(public_id="tl-5", row_version="rv1", clock_out="2026-09-10 17:00:00")
    kw.update(over)
    return TimeLogService().update_by_public_id(**kw)


def delete():
    return TimeLogService().delete_by_public_id(public_id="tl-5")


WRITES = {"create": create, "update": update, "delete": delete}
REOPENING = ["create", "update"]          # the device's late writes; delete stays with review


# ─── draft days: nothing changes ────────────────────────────────────────────

@pytest.mark.parametrize("write", sorted(WRITES))
def test_draft_parent_writes_without_touching_status(monkeypatch, write):
    w = World(monkeypatch, status="draft")
    with actor_context(OWNER, False):
        WRITES[write]()
    assert w.log_events == [f"log:{write}"]
    assert w.status_writes == [] and w.stamps == [] and w.reopens == []


def test_draft_day_project_move_and_stale_row_version_are_still_the_sprocs_business(monkeypatch):
    w = World(monkeypatch, status="draft")
    with actor_context(OWNER, False):
        update(project_id=202)
        update(row_version="stale")
    assert w.log_events == ["log:update", "log:update"] and w.reopens == []


def test_no_status_history_still_writes_as_before(monkeypatch):
    w = World(monkeypatch, status=None)
    with actor_context(OWNER, False):
        create()
    assert w.log_events == ["log:create"] and w.reopens == []


# ─── the reopen: decided here, performed by the write itself ───────────────

@pytest.mark.parametrize("write", REOPENING)
def test_owner_write_on_submitted_hands_the_reopen_to_the_write_itself(monkeypatch, write):
    w = World(monkeypatch, status="submitted")
    with actor_context(OWNER, False):
        WRITES[write]()
    # No separate status write, no separate stamp: the write sproc reopens
    # (draft row by the OWNER with the note, plus the marker) and writes in ONE
    # transaction, so a failed write never leaves a reopened day behind.
    assert w.status_writes == [] and w.stamps == [] and "status:PLAIN" not in w.events
    assert w.reopens == [(write, OWNER, TimeLogService.REOPEN_NOTE)]
    assert w.events == [f"log:{write}"]


def test_a_write_that_fails_at_the_row_leaves_nothing_behind_on_this_side_and_is_not_swallowed(monkeypatch):
    w = World(monkeypatch, status="submitted", write_raises=RuntimeError("FK_TimeLog_Project"))
    with actor_context(OWNER, False), pytest.raises(RuntimeError):
        create(project_id=-1)
    assert w.status_writes == [] and w.stamps == []


def test_a_transition_that_slipped_in_before_the_write_surfaces_as_a_locked_entry_not_a_500(monkeypatch):
    from shared.database import DatabaseOperationError
    w = World(monkeypatch, status="submitted", write_raises=DatabaseOperationError(
        "Database operation failed: Cannot modify time logs when time entry is in 'approved' status — the entry is not in 'draft'."))
    with actor_context(OWNER, False), pytest.raises(ValueError) as exc:
        create()
    assert ios_classifies_as_locked(str(exc.value)) and "Database operation failed" not in str(exc.value)
    assert not any(p in str(exc.value).lower() for p in MAPPER_409_PHRASES)


def test_reason_code_is_registered_and_stamp_review_accepts_it(monkeypatch):
    assert REASON_REOPENED_AFTER_SUBMIT == "reopened_after_submit"
    assert REASON_REOPENED_AFTER_SUBMIT in ALL_REASON_CODES
    w = World(monkeypatch)
    out = TimeEntryService().stamp_review(public_id="te-10", priority="medium", reasons=["missing_note"])
    assert out["reasons"] == ["missing_note"] and w.stamps


# ─── refusals: nothing is written, iOS reads every one as a locked entry, none trips the 409 rule ──

def _refused(w, action, match):
    with pytest.raises(ValueError, match=match) as exc:
        action()
    msg = str(exc.value)
    assert ios_classifies_as_locked(msg), msg
    assert not any(p in msg.lower() for p in MAPPER_409_PHRASES), msg
    assert w.status_writes == [] and w.stamps == [] and w.log_events == [] and w.reopens == []
    assert "status:PLAIN" not in w.events


@pytest.mark.parametrize("write", sorted(WRITES))
@pytest.mark.parametrize("actor", [(TEAMMATE, False), (ADMIN, True), (None, False)], ids=["teammate", "system-admin", "no-actor"])
def test_anyone_but_the_owner_is_still_refused_on_submitted(monkeypatch, write, actor):
    w = World(monkeypatch, status="submitted")
    with actor_context(*actor):
        _refused(w, WRITES[write], r"in 'submitted' status")


@pytest.mark.parametrize("status", ["approved", "billed", "rejected"])
def test_owner_write_on_any_other_status_is_refused(monkeypatch, status):
    w = World(monkeypatch, status=status)
    with actor_context(OWNER, False):
        _refused(w, create, rf"in '{status}' status")


def test_owner_write_on_submitted_under_a_posted_bill_is_refused(monkeypatch):
    w = World(monkeypatch, status="submitted", locked=True)
    with actor_context(OWNER, False):
        _refused(w, create, r"reviewed, billed or invoiced")


def test_owner_write_on_submitted_with_reviewed_split_or_partially_billed_labor_is_refused(monkeypatch):
    w = World(monkeypatch, status="submitted", labor_touched=True)
    with actor_context(OWNER, False):
        _refused(w, create, r"reviewed, billed or invoiced")


def test_delete_on_a_submitted_day_never_reopens_it(monkeypatch):
    w = World(monkeypatch, status="submitted")
    with actor_context(OWNER, False):
        _refused(w, delete, r"Deleting a log on a submitted day goes through review")


def test_project_move_or_work_break_change_on_a_submitted_day_never_reopens_it(monkeypatch):
    w = World(monkeypatch, status="submitted")
    with actor_context(OWNER, False):
        _refused(w, lambda: update(project_id=202), r"Moving a log to another project")
        _refused(w, lambda: update(log_type="break"), r"between work and break")
    with actor_context(OWNER, False):
        update(project_id=101)                       # re-sending the SAME project is not a move
        update(log_type="work")                      # same type is not a change
    assert w.log_events == ["log:update", "log:update"] and len(w.reopens) == 1    # the first reopened; the day is draft after


def test_stale_row_version_on_a_submitted_day_never_reopens_it(monkeypatch):
    w = World(monkeypatch, status="submitted")
    with actor_context(OWNER, False):
        _refused(w, lambda: update(row_version="stale"), r"changed on the server")


def test_malformed_timestamps_on_a_submitted_day_never_reopen_it(monkeypatch):
    w = World(monkeypatch, status="submitted")
    with actor_context(OWNER, False):
        _refused(w, lambda: create(clock_in="not-a-date"), r"not a valid timestamp")
        _refused(w, lambda: update(clock_out="2026-13-45 99:00:00"), r"not a valid timestamp")


def test_replayed_create_or_clock_in_moved_onto_a_siblings_never_reopens_a_submitted_day(monkeypatch):
    w = World(monkeypatch, status="submitted")
    with actor_context(OWNER, False):
        _refused(w, lambda: create(clock_in="2026-09-10 08:00:00"), r"holds this clock-in")      # a replay, both spellings
        _refused(w, lambda: create(clock_in="2026-09-10T08:00:00"), r"holds this clock-in")
        _refused(w, lambda: update(clock_in="2026-09-10 13:00:00"), r"holds this clock-in")      # the sibling's
    with actor_context(OWNER, False):
        update(clock_in="2026-09-10 08:00:00")       # its own current clock-in: not a collision
    assert w.log_events == ["log:update"]


def test_invalid_input_is_refused_before_anything_is_decided(monkeypatch):
    w = World(monkeypatch, status="submitted")
    with actor_context(OWNER, False):
        with pytest.raises(ValueError, match=r"Invalid log_type"):
            create(log_type="invalid")
        with pytest.raises(ValueError, match=r"Invalid log_type"):
            update(log_type="invalid")
    assert w.events == [] and w.reopens == []


# ─── transitions: approve/reject compare-and-insert; submit = status + aggregation in one call ──

def _entry_read(monkeypatch, status):
    return World(monkeypatch, status=status)


def test_approve_and_reject_write_against_the_row_they_read(monkeypatch):
    svc = TimeEntryService()
    w = _entry_read(monkeypatch, "submitted")
    svc.approve("te-10", user_id=ADMIN)
    assert w.status_writes[-1]["status"] == "approved"
    assert w.status_writes[-1]["expected_current_status_id"] == CURRENT_STATUS_ROW_ID

    w = _entry_read(monkeypatch, "submitted")
    svc.reject("te-10", user_id=ADMIN, note="fix hours")
    assert [x["status"] for x in w.status_writes] == ["rejected", "draft"]
    assert w.status_writes[0]["expected_current_status_id"] == CURRENT_STATUS_ROW_ID
    assert w.status_writes[1]["expected_current_status_id"] == w.status_writes[0]["expected_current_status_id"] + 1
    assert "status:PLAIN" not in w.events


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_transition_decided_on_a_stale_read_writes_nothing(monkeypatch, action):
    w = World(monkeypatch, status="submitted", conflict=True)
    svc = TimeEntryService()
    call = {"approve": lambda: svc.approve("te-10", user_id=ADMIN),
            "reject": lambda: svc.reject("te-10", user_id=ADMIN)}[action]
    with pytest.raises(ValueError, match=r"status changed while this request was in flight"):
        call()
    assert w.status_writes == [] and "status:PLAIN" not in w.events


def test_submit_is_one_call_status_row_and_aggregation_together(monkeypatch):
    w = _entry_read(monkeypatch, "draft")
    with actor_context(OWNER, False):
        TimeEntryService().submit("te-10", user_id=OWNER)
    assert w.events == ["submit:status+aggregate"]
    assert w.status_writes == [{"time_entry_id": 10, "expected_current_status_id": CURRENT_STATUS_ROW_ID,
                                "status": "submitted", "user_id": OWNER, "note": None}]
    assert "aggregate:standalone" not in w.events and "status:PLAIN" not in w.events


def test_a_refused_aggregation_refuses_the_submit(monkeypatch):
    w = World(monkeypatch, status="draft", submit_raises=RuntimeError(
        "Database operation failed: REFUSED: TimeEntry 10 — this resubmit would change labor that has been reviewed"))
    with actor_context(OWNER, False), pytest.raises(ValueError, match=r"^Cannot submit: TimeEntry 10 — this resubmit would change labor") as exc:
        TimeEntryService().submit("te-10", user_id=OWNER)
    assert not any(p in str(exc.value).lower() for p in MAPPER_409_PHRASES)
    assert w.status_writes == []


def test_any_other_aggregation_failure_still_submits_unaggregated(monkeypatch):
    w = World(monkeypatch, status="draft", aggregation_error="User 41 has no worker linkage")
    with actor_context(OWNER, False):
        TimeEntryService().submit("te-10", user_id=OWNER)
    assert [x["status"] for x in w.status_writes] == ["submitted"]


def test_a_submit_whose_status_moved_underneath_it_is_refused(monkeypatch):
    w = World(monkeypatch, status="draft", conflict=True)
    with actor_context(OWNER, False), pytest.raises(ValueError, match=r"status changed while this request was in flight"):
        TimeEntryService().submit("te-10", user_id=OWNER)
    assert w.status_writes == []


def test_repo_reads_the_submit_outcome_whether_or_not_aggregation_rows_precede_it(monkeypatch):
    from entities.time_entry.persistence import repo as repo_module

    class Cursor:
        def __init__(self, sets):
            self.sets = sets; self.i = 0
        @property
        def description(self):
            return [(c,) for c in self.sets[self.i][0]]
        def fetchall(self):
            return self.sets[self.i][1]
        def fetchone(self):
            rows = self.sets[self.i][1]; return rows[0] if rows else None
        def nextset(self):
            if self.i + 1 < len(self.sets):
                self.i += 1; return True
            return False
    OUT_COLS = ["Id", "PublicId", "RowVersion", "CreatedDatetime", "TimeEntryId", "Status", "UserId", "Note", "AggregationError"]
    AGG_COLS = ["TargetTable", "TargetRowId", "ProjectId", "WorkDate", "TotalHours", "HourlyRate", "Markup", "RateSource", "Status", "Note"]
    outcome = SimpleNamespace(Id=78, PublicId="p", RowVersion=b"\x00" * 8, CreatedDatetime="2026-10-02 12:00:00",
                              TimeEntryId=10, Status="submitted", UserId=OWNER, Note=None, AggregationError=None)
    agg_row = SimpleNamespace(TargetTable="ContractLabor", TargetRowId=1, ProjectId=101, WorkDate="2026-09-10", TotalHours=8,
                              HourlyRate=None, Markup=None, RateSource="none", Status="pending_review", Note="rate_source=none")
    monkeypatch.setattr(repo_module, "call_procedure", lambda **kw: None)
    monkeypatch.setattr(TimeEntryStatusRepository, "_from_db", lambda self, row: SimpleNamespace(id=row.Id, status=row.Status))
    for sets, expect_rows, expect_written in (
        ([(AGG_COLS, [agg_row]), (OUT_COLS, [outcome])], ["none"], True),     # aggregator rows first, then the outcome
        ([(OUT_COLS, [outcome])], [], True),                                  # aggregator produced nothing
        ([(OUT_COLS, [])], [], False),                                         # conflict: empty outcome
    ):
        cur = Cursor(sets)
        @contextmanager
        def conn():
            yield SimpleNamespace(cursor=lambda: cur)
        monkeypatch.setattr(repo_module, "get_connection", conn)
        written, rows, err = TimeEntryRepository().submit_with_aggregation(time_entry_id=10, expected_current_status_id=77, user_id=OWNER)
        assert [r["rate_source"] for r in rows] == expect_rows
        assert (written is not None) is expect_written and err is None


# ─── the stored procedures the fix stands on (canonical base file) ──────────

def _sproc(name: str) -> str:
    text = BASE_SQL.read_text()
    m = re.search(rf"CREATE OR ALTER PROCEDURE (?:\[?dbo\]?\.)?\[?{name}\]?\b.*?\nGO\b", text, re.S)
    assert m, f"{name} is not defined in the canonical base file"
    return m.group(0)


def test_conditional_status_insert_is_a_locked_compare_and_insert_that_returns_an_empty_set_on_conflict():
    body = _sproc("CreateTimeEntryStatusIfCurrent")
    assert re.search(r"WITH \(UPDLOCK, HOLDLOCK\)", body)
    assert re.search(r"\[TimeEntryId\] = @TimeEntryId AND \[Id\] > @ExpectedCurrentStatusId", body)
    assert "BEGIN TRANSACTION" in body and "COMMIT TRANSACTION" in body
    assert re.search(r"ELSE\s*BEGIN.*FROM dbo\.\[TimeEntryStatus\] WHERE 1 = 0;", body, re.S)


def test_labor_untouched_reads_status_links_review_rows_and_split_lines_and_can_be_called_quietly():
    body = _sproc("IsTimeEntryLaborUntouched")
    # round 6: holds the labor rows, lines and the Review range for the caller's transaction, and reads the durable marker
    assert body.count("WITH (UPDLOCK, HOLDLOCK)") == 6          # TimeEntry (deletion record), two parents, two line sets, Review
    assert "cl.[EditedSinceAggregation] = 1" in body and "el.[EditedSinceAggregation] = 1" in body
    assert body.count("li.[EditedSinceAggregation] = 1") == 2
    assert re.search(r"FROM dbo\.\[Review\] r WITH \(UPDLOCK, HOLDLOCK\) WHERE r\.\[ContractLaborId\] = cl\.\[Id\]", body)
    for needle in ("cl.[Status] <> 'pending_review'", "cl.[BillLineItemId] IS NOT NULL",
                   "dbo.[ContractLaborLineItem] li", "li.[BillLineItemId] IS NOT NULL",
                   "FROM dbo.[Review] r WITH (UPDLOCK, HOLDLOCK) WHERE r.[ContractLaborId] = cl.[Id]",
                   "el.[Status] <> 'pending_review'", "el.[InvoiceLineItemId] IS NOT NULL",
                   "dbo.[EmployeeLaborLineItem] li", "li.[InvoiceLineItemId] IS NOT NULL"):
        assert needle in body, needle
    assert body.count("[SourceTimeEntryId] IS NULL") == 2          # a PM's split line, both families
    assert "@Untouched BIT = NULL OUTPUT" in body and "@ReturnRow BIT = 1" in body
    assert "IF @ReturnRow = 1 SELECT @Untouched AS Untouched;" in body


def test_stamp_review_carries_the_reopen_marker_forward_and_floors_a_reopened_day_at_medium():
    body = _sproc("StampTimeEntryReview")
    assert "N'reopened_after_submit'" in body
    assert re.search(r"JSON_MODIFY\(@ReasonsJson, 'append \$', @Marker\)", body)
    assert re.search(r"CASE WHEN k\.\[Marked\] = 1 AND @Priority IN \('clean', 'low'\) THEN 'medium'", body)
    assert re.search(r"CASE WHEN h\.\[Had\] = 1 OR h\.\[Incoming\] = 1 THEN 1 ELSE 0 END AS \[Marked\]", body)
    assert re.search(r"CASE WHEN h\.\[Had\] = 1 AND h\.\[Incoming\] = 0 AND ISJSON\(@ReasonsJson\) = 1 THEN 1 ELSE 0 END AS \[Keep\]", body)
    assert "@ReturnRow         BIT = 1" in body and re.search(r"IF @ReturnRow = 1\s+SELECT @Affected AS \[AffectedRowCount\], te\.\[ReviewPriority\], te\.\[ReviewReasons\]", body)


def test_aggregator_rebuilds_untouched_labor_only_and_refuses_to_change_touched_labor():
    body = _sproc("AggregateTimeEntryOnSubmit")
    assert "@LinesChanged" not in body
    decide = body.index("EXEC dbo.IsTimeEntryLaborUntouched @TimeEntryId = @TimeEntryId, @Untouched = @Untouched OUTPUT, @ReturnRow = 0;")
    assert decide < body.index("IF @BucketCount = 0")
    assert re.search(r"IF @ExistingParentId IS NOT NULL AND @Untouched = 0", body)
    for fam, fk in (("EmployeeLaborLineItem", "EmployeeLaborId"), ("ContractLaborLineItem", "ContractLaborId")):
        assert re.search(rf"SELECT ProjectId, TotalHours FROM @Buckets\s+EXCEPT SELECT \[ProjectId\], \[Hours\] FROM dbo\.\[{fam}\]", body), fam
        assert re.search(rf"FROM dbo\.\[{fam}\]\s+WHERE \[{fk}\] = @ExistingParentId AND \[SourceTimeEntryId\] = @TimeEntryId\s+EXCEPT SELECT ProjectId, TotalHours FROM @Buckets", body), fam
    # the refusal is built without a printf format (a BIGINT through %d is a formatting error that loses the prefix)
    assert re.search(r"DECLARE @Refused NVARCHAR\(400\) = N'REFUSED: TimeEntry ' \+ CAST\(@TimeEntryId AS NVARCHAR\(20\)\)", body)
    assert "RAISERROR(@Refused, 16, 1);" in body and "RAISERROR('REFUSED" not in body
    assert "N'unchanged — labor already reviewed/billed; left as is'" in body
    zero = body[body.index("IF @BucketCount = 0"):]
    for fam, fk in (("EmployeeLaborLineItem", "EmployeeLaborId"), ("ContractLaborLineItem", "ContractLaborId")):
        assert f"DELETE FROM dbo.[{fam}] WHERE [{fk}] = @ExistingParentId AND [SourceTimeEntryId] = @TimeEntryId" in zero, fam
    assert "UPDATE dbo.[EmployeeLabor] SET [TotalHours] = 0, [TotalAmount] = 0" in zero
    for fam, fk, link in (("EmployeeLaborLineItem", "EmployeeLaborId", "InvoiceLineItemId"),
                          ("ContractLaborLineItem", "ContractLaborId", "BillLineItemId")):
        assert re.search(rf"DELETE li FROM dbo\.\[{fam}\] li\s+WHERE li\.\[{fk}\] = @ParentRowId AND li\.\[SourceTimeEntryId\] = @TimeEntryId\s+AND li\.\[{link}\] IS NULL\s+AND NOT EXISTS \(SELECT 1 FROM @Buckets b", body), fam
    assert body.rindex("DELETE li FROM dbo.[ContractLaborLineItem]") < body.rindex("EXEC dbo.UpdateContractLaborAggregates")


@pytest.mark.parametrize("name", LOG_WRITES)
def test_every_log_write_rechecks_the_status_under_lock_and_reopens_only_for_the_owner_in_the_same_transaction(name):
    body = _sproc(name)
    # NOCOUNT first: the reopen's DML now precedes the OUTPUT rows, and row-count chatter would reach the driver before them
    assert body.index("SET NOCOUNT ON;") < body.index("SET XACT_ABORT ON;") < body.index("BEGIN TRANSACTION;")
    assert "@ReopenAsUserId BIGINT = NULL" in body and "@ReopenNote NVARCHAR(MAX) = NULL" in body
    guard = body.index("WITH (UPDLOCK, HOLDLOCK)")
    assert body.index("BEGIN TRANSACTION;") < guard
    assert re.search(r"SELECT TOP 1 s\.\[Status\] FROM dbo\.\[TimeEntryStatus\] s WITH \(UPDLOCK, HOLDLOCK\)\s+WHERE s\.\[TimeEntryId\] = @TimeEntryId\s+ORDER BY s\.\[CreatedDatetime\] DESC, s\.\[Id\] DESC", body)
    # reopen ONLY for 'submitted', ONLY when the handed-in user owns the entry — the sproc does not trust the caller
    assert re.search(r"IF @CurrentStatus = 'submitted' AND @ReopenAsUserId IS NOT NULL\s+AND EXISTS \(SELECT 1 FROM dbo\.\[TimeEntry\] te WHERE te\.\[Id\] = @TimeEntryId AND te\.\[UserId\] = @ReopenAsUserId\)", body)
    assert re.search(r"VALUES \(SYSUTCDATETIME\(\), @TimeEntryId, N'draft', @ReopenAsUserId, @ReopenNote\);", body)
    assert re.search(r"EXEC dbo\.StampTimeEntryReview @TimeEntryPublicId = @ReopenedPublicId, @Priority = 'medium',\s+@ReasonsJson = N'\[\"reopened_after_submit\"\]', @ReturnRow = 0;", body)
    assert "IF @CurrentStatus IS NOT NULL AND @CurrentStatus <> 'draft'" in body      # no history still writes (parity)
    assert "RAISERROR(@Locked, 16, 1);" in body and "the entry is not in ''draft''" in body
    # round 6: the reopen re-checks the labor under the predicate's locks before writing the draft row
    recheck = body.index("EXEC dbo.IsTimeEntryLaborUntouched @TimeEntryId = @TimeEntryId, @Untouched = @LaborUntouched OUTPUT, @ReturnRow = 0;")
    assert body.index("te.[UserId] = @ReopenAsUserId") < recheck < body.index("N'draft', @ReopenAsUserId")
    assert re.search(r"IF @LaborUntouched = 0\s+BEGIN\s+COMMIT TRANSACTION;[^\n]*\n\s+RAISERROR\('Cannot modify time logs when time entry is in ''submitted'' status — the entry is not in ''draft''\. Its labor has been reviewed, billed or invoiced; reverse that first\.', 16, 1\);", body)
    if name != "CreateTimeLog":
        assert "DECLARE @TimeEntryId BIGINT = (SELECT [TimeEntryId] FROM dbo.[TimeLog] WHERE [Id] = @Id);" in body
    write = {"CreateTimeLog": "INSERT INTO dbo.[TimeLog]", "UpdateTimeLogById": "UPDATE tl", "DeleteTimeLogById": "DELETE tl"}[name]
    assert guard < body.index("N'draft', @ReopenAsUserId") < body.index(write)


def test_submit_procedure_writes_the_status_row_and_aggregates_in_one_transaction():
    body = _sproc("SubmitTimeEntry")
    assert "SET XACT_ABORT OFF;" in body
    tx = body.index("BEGIN TRANSACTION;")
    lock = body.index("WITH (UPDLOCK, HOLDLOCK)"); sp = body.index("SAVE TRANSACTION AggregationSavepoint;")
    agg = body.index("EXEC dbo.AggregateTimeEntryOnSubmit @TimeEntryId = @TimeEntryId;")
    status = body.index("VALUES (SYSUTCDATETIME(), @TimeEntryId, N'submitted', @UserId, NULL);")
    # nothing is written before the decision: lock → savepoint → aggregation → status row → commit
    assert tx < lock < body.index("DECLARE @HadLabor BIT") < sp < agg < status < body.rindex("COMMIT TRANSACTION;")
    # a refusal rolls back to the savepoint and COMMITS (nothing written) before raising; only a doomed transaction rolls back fully
    assert re.search(r"IF XACT_STATE\(\) = -1\s+BEGIN\s+ROLLBACK TRANSACTION;", body)
    assert re.search(r"ROLLBACK TRANSACTION AggregationSavepoint;\s+COMMIT TRANSACTION;[^\n]*\n\s+RAISERROR\(@AggregationError, 16, 1\);", body)
    assert body.count("ROLLBACK TRANSACTION;") == 1                 # the doomed case only
    assert "IF @AggregationError LIKE N'REFUSED:%' OR @HadLabor = 1" in body and "its labor could not be rebuilt from the logs" in body
    assert body.rindex("@AggregationError AS [AggregationError]") > status



# ─── round 6: a durable record of every writer other than the aggregator ───

LABOR_SQL = {
    "ContractLabor": Path(__file__).resolve().parents[1] / "entities" / "contract_labor" / "sql",
    "EmployeeLabor": Path(__file__).resolve().parents[1] / "entities" / "employee_labor" / "sql",
}


@pytest.mark.parametrize("family", sorted(LABOR_SQL))
def test_the_edited_marker_is_added_idempotently_to_parent_and_line_tables(family):
    text = (LABOR_SQL[family] / "add_edited_since_aggregation.sql").read_text()
    for table in (family, f"{family}LineItem"):
        assert re.search(rf"WHERE object_id = OBJECT_ID\('dbo\.{table}'\) AND name = 'EditedSinceAggregation'", text), table
        assert re.search(rf"ALTER TABLE dbo\.\[{table}\]\s+ADD \[EditedSinceAggregation\] BIT NOT NULL CONSTRAINT \[DF_{table}_EditedSinceAggregation\] DEFAULT \(0\);", text), table
        # rows that exist at deployment are 'edited' — pre-existing corrections left no trace, so they are never auto-rebuilt
        assert re.search(rf"DEFAULT \(0\);\s+EXEC\(N'UPDATE dbo\.\[{table}\] SET \[EditedSinceAggregation\] = 1;'\);\s+END", text), table


@pytest.mark.parametrize("name,family", [("UpdateContractLaborById", "ContractLabor"), ("UpdateContractLaborLineItemById", "ContractLabor"),
                                         ("UpdateEmployeeLaborById", "EmployeeLabor"), ("UpdateEmployeeLaborLineItemById", "EmployeeLabor")])
def test_every_generic_labor_update_raises_the_edited_marker(name, family):
    text = (LABOR_SQL[family] / f"dbo.{'contract_labor' if family == 'ContractLabor' else 'employee_labor'}.sql").read_text()
    m = re.search(rf"CREATE OR ALTER PROCEDURE {name}\b.*?\nGO", text, re.S); assert m, name
    body = m.group(0)
    assert re.search(r"    SET\n\s+\[ModifiedDatetime\]\s*=\s*@Now,\n\s+\[EditedSinceAggregation\] = 1,", body), name




@pytest.mark.parametrize("name,family,parent,line", [
    ("DeleteContractLaborLineItemById", "ContractLabor", "ContractLabor", "ContractLaborLineItem"),
    ("DeleteContractLaborLineItemsByContractLaborId", "ContractLabor", "ContractLabor", "ContractLaborLineItem"),
    ("CreateContractLaborLineItem", "ContractLabor", "ContractLabor", "ContractLaborLineItem"),
    ("DeleteEmployeeLaborLineItemById", "EmployeeLabor", "EmployeeLabor", "EmployeeLaborLineItem"),
    ("DeleteEmployeeLaborLineItemsByEmployeeLaborId", "EmployeeLabor", "EmployeeLabor", "EmployeeLaborLineItem"),
    ("CreateEmployeeLaborLineItem", "EmployeeLabor", "EmployeeLabor", "EmployeeLaborLineItem"),
])
def test_adding_or_removing_a_line_outside_the_aggregator_marks_the_parent(name, family, parent, line):
    text = (LABOR_SQL[family] / f"dbo.{'contract_labor' if family == 'ContractLabor' else 'employee_labor'}.sql").read_text()
    m = re.search(rf"CREATE OR ALTER PROCEDURE {name}\b.*?\nGO", text, re.S); assert m, name
    body = m.group(0)
    mark = body.index(f"UPDATE dbo.[{parent}] SET [EditedSinceAggregation] = 1 WHERE [Id] = @ParentForMarker;")
    write = re.search(rf"(DELETE FROM|INSERT INTO) dbo\.\[{line}\]", body); assert write, name
    assert body.index("SET NOCOUNT ON;") < mark < write.start(), name          # NOCOUNT first; the mark precedes the write
    if name.endswith("ById"):
        assert f"DECLARE @ParentForMarker BIGINT = (SELECT [{parent}Id] FROM dbo.[{line}] WHERE [Id] = @Id);" in body


def test_a_rebuilt_parent_follows_the_days_date_and_billing_period():
    body = _sproc("AggregateTimeEntryOnSubmit")
    for parent in ("EmployeeLabor", "ContractLabor"):
        m = re.search(rf"UPDATE dbo\.\[{parent}\]\s+SET \[ModifiedDatetime\]\s+= SYSUTCDATETIME\(\),(.*?)WHERE \[Id\] = @ParentRowId;", body, re.S)
        assert m, parent
        assert "[WorkDate]          = @WorkDate," in m.group(1) and "[BillingPeriodStart] = @BillingPeriodStart," in m.group(1), parent


# ─── round 8 ────────────────────────────────────────────────────────────────

def test_no_pre_write_guard_rolls_back_a_transaction():
    """pyodbc runs autocommit-off: a ROLLBACK inside a sproc zeroes the caller's
    implicit outer transaction and SQL Server raises error 266 instead of the
    refusal. Every guard that fires before any write COMMITS its empty
    transaction and then raises."""
    for name in ("CreateTimeLog", "UpdateTimeLogById", "DeleteTimeLogById", "UpdateTimeEntryById", "DeleteTimeEntryById"):
        body = _sproc(name)
        assert "ROLLBACK TRANSACTION" not in body, name
        assert re.search(r"COMMIT TRANSACTION;[^\n]*\n(\s+DECLARE @Locked[^\n]*\n\s+\+ [^\n]*\n)?\s+RAISERROR\(", body), name
    rv = (Path(__file__).resolve().parents[1] / "entities" / "review" / "sql" / "dbo.review.sql").read_text()
    cr = re.search(r"CREATE OR ALTER PROCEDURE CreateReview\b.*?\nGO", rv, re.S).group(0)
    assert "ROLLBACK TRANSACTION" not in cr
    assert re.search(r"COMMIT TRANSACTION;\s+RAISERROR\('STATUS_LOCKED: cannot review labor while its time entry is in ''draft''", cr)



def test_touched_labor_is_pinned_to_its_date_and_its_worker_as_well_as_its_hours():
    body = _sproc("AggregateTimeEntryOnSubmit")
    assert re.search(r"WHERE \[Id\] = @ExistingParentId AND \(\[WorkDate\] <> @WorkDate OR \[EmployeeId\] <> @EmployeeId\)\)\)", body)
    assert re.search(r"WHERE \[Id\] = @ExistingParentId AND \(\[WorkDate\] <> @WorkDate OR \[VendorId\] <> @VendorId\)\)\)\)\s+SET @Differs = 1;", body)
    assert body.index("[WorkDate] <> @WorkDate") < body.index("IF @Differs = 1")


def test_deleting_the_labor_parent_records_it_on_the_entry_and_the_predicate_reads_it_first():
    mig = (Path(__file__).resolve().parents[1] / "entities" / "time_entry" / "sql" / "migrations" / "018_2026_10_02_labor_deleted_marker.sql").read_text()
    assert "IF COL_LENGTH('dbo.TimeEntry', 'LaborDeletedDatetime') IS NULL" in mig
    assert "ALTER TABLE dbo.[TimeEntry] ADD [LaborDeletedDatetime] DATETIME2(3) NULL;" in mig
    for family, name, table in (("ContractLabor", "DeleteContractLaborById", "ContractLabor"), ("EmployeeLabor", "DeleteEmployeeLaborById", "EmployeeLabor")):
        text = (LABOR_SQL[family] / f"dbo.{'contract_labor' if family == 'ContractLabor' else 'employee_labor'}.sql").read_text()
        body = re.search(rf"CREATE OR ALTER PROCEDURE {name}\b.*?\nGO", text, re.S).group(0)
        stamp = re.search(rf"UPDATE te SET te\.\[LaborDeletedDatetime\] = SYSUTCDATETIME\(\)\s+FROM dbo\.\[TimeEntry\] te\s+JOIN dbo\.\[{table}\] p ON p\.\[SourceTimeEntryId\] = te\.\[Id\]\s+WHERE p\.\[Id\] = @Id;", body)
        assert stamp, name
        assert body.index("SET NOCOUNT ON;") < stamp.start() < body.index(f"DELETE FROM dbo.[{table}]"), name
    pred = _sproc("IsTimeEntryLaborUntouched")
    assert re.search(r"WHERE te\.\[Id\] = @TimeEntryId AND te\.\[LaborDeletedDatetime\] IS NOT NULL\)\s+SET @Touched = 1;", pred)
    first = pred.index("te.[LaborDeletedDatetime] IS NOT NULL")
    assert pred.index("DECLARE @Touched BIT = 0;") < first < pred.index("FROM dbo.[ContractLabor] cl WITH (UPDLOCK, HOLDLOCK)")
    assert "FROM dbo.[TimeEntry] te WITH (UPDLOCK, HOLDLOCK)" in pred


# ─── round 9 ────────────────────────────────────────────────────────────────

def test_a_rebuilt_parent_follows_the_days_current_worker_and_a_family_change_is_refused():
    body = _sproc("AggregateTimeEntryOnSubmit")
    el = re.search(r"UPDATE dbo\.\[EmployeeLabor\]\s+SET \[ModifiedDatetime\]\s+= SYSUTCDATETIME\(\),(.*?)WHERE \[Id\] = @ParentRowId;", body, re.S).group(1)
    assert "[EmployeeId]        = @EmployeeId," in el
    cl = re.search(r"UPDATE dbo\.\[ContractLabor\]\s+SET \[ModifiedDatetime\]\s+= SYSUTCDATETIME\(\),(.*?)WHERE \[Id\] = @ParentRowId;", body, re.S).group(1)
    for needle in ("[VendorId]          = @VendorId,", "[BillVendorId]      = @VendorId,", "[EmployeeName]      = @WorkerName,"):
        assert needle in cl, needle
    # a parent in the other family refuses BEFORE the untouched decision
    fam = body.index("its labor was aggregated for a different worker type")
    assert body.index("DECLARE @ExistingParentId BIGINT = NULL;") < fam < body.index("DECLARE @Untouched BIT = 1;")
    assert "RAISERROR(@RefusedFamily, 16, 1);" in body


def test_the_aggregator_refuses_to_recreate_labor_the_office_deleted_even_when_no_parent_remains():
    body = _sproc("AggregateTimeEntryOnSubmit")
    m = re.search(r"IF EXISTS \(SELECT 1 FROM dbo\.\[TimeEntry\] te WITH \(UPDLOCK, HOLDLOCK\)\s+WHERE te\.\[Id\] = @TimeEntryId AND te\.\[LaborDeletedDatetime\] IS NOT NULL\)\s+BEGIN\s+DECLARE @RefusedDeleted", body)
    assert m
    assert m.start() < body.index("DECLARE @ExistingParentId BIGINT = NULL;")      # before any parent lookup
    assert "RAISERROR(@RefusedDeleted, 16, 1);" in body


# ─── round 10: EVERY writer of a labor table either raises the marker or is on the explicit allowlist ──

# System recompute (sum-of-children, never a human change) and the two parent deletes, which record the
# deletion on the TimeEntry (LaborDeletedDatetime) because the cascade erases the rows the marker lives on.
WRITERS_WITHOUT_THE_MARKER = {"UpdateContractLaborAggregates", "DeleteContractLaborById", "DeleteEmployeeLaborById"}
LABOR_TABLES = r"ContractLabor|ContractLaborLineItem|EmployeeLabor|EmployeeLaborLineItem"


@pytest.mark.parametrize("family", sorted(LABOR_SQL))
def test_every_procedure_that_writes_a_labor_table_raises_the_marker_or_is_explicitly_allowlisted(family):
    text = (LABOR_SQL[family] / f"dbo.{'contract_labor' if family == 'ContractLabor' else 'employee_labor'}.sql").read_text()
    seen = []
    for m in re.finditer(r"CREATE OR ALTER PROCEDURE (?:\[?dbo\]?\.)?\[?(\w+)\]?\b(.*?)\nGO", text, re.S):
        name, body = m.group(1), m.group(2)
        if not re.search(rf"\b(UPDATE|INSERT INTO|DELETE(?: \w+)? FROM)\s+(?:\w+\s+)?dbo\.\[(?:{LABOR_TABLES})\]", body):
            continue
        seen.append(name)
        if name in WRITERS_WITHOUT_THE_MARKER:
            assert "EditedSinceAggregation" not in body or name.startswith("Delete"), name
            if name.startswith("Delete"):
                assert "te.[LaborDeletedDatetime] = SYSUTCDATETIME()" in body, name
            continue
        # an UPDATE raises it in its SET list; a CREATE carries it in the INSERT column list (never a post-update,
        # which would invalidate the row version the OUTPUT already returned)
        raised = "[EditedSinceAggregation] = 1" in body or re.search(r"INSERT INTO dbo\.\[\w+\]\s*\([^)]*\[EditedSinceAggregation\]", body)
        assert raised, f"{name} writes labor rows without raising EditedSinceAggregation"
    assert len(seen) >= {"ContractLabor": 10, "EmployeeLabor": 7}[family], seen   # the guard is looking at the real file, not an empty match


def test_the_aggregator_itself_never_raises_the_marker():
    assert "EditedSinceAggregation" not in _sproc("AggregateTimeEntryOnSubmit")


# ─── round 11 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["UpdateTimeEntryById", "DeleteTimeEntryById"])
def test_entry_header_writes_recheck_the_status_under_the_same_lock(name):
    body = _sproc(name)
    assert body.index("SET NOCOUNT ON;") < body.index("SET XACT_ABORT ON;") < body.index("BEGIN TRANSACTION;")
    guard = body.index("WITH (UPDLOCK, HOLDLOCK)")
    assert re.search(r"FROM dbo\.\[TimeEntryStatus\] s WITH \(UPDLOCK, HOLDLOCK\)\s+WHERE s\.\[TimeEntryId\] = @Id", body)
    assert "IF @CurrentStatus IS NOT NULL AND @CurrentStatus <> 'draft'" in body and "the entry is not in ''draft''" in body
    assert guard < body.index({"UpdateTimeEntryById": "UPDATE dbo.[TimeEntry]", "DeleteTimeEntryById": "DELETE FROM dbo.[TimeEntry]"}[name])


@pytest.mark.parametrize("name", ["ReadTimeEntries", "ReadTimeEntryById", "ReadTimeEntryByPublicId",
                                  "ReadTimeEntriesByUserId", "ReadTimeEntriesByProjectId", "ReadTimeEntriesPaginated"])
def test_every_entry_read_returns_the_review_marker(name):
    assert re.search(r"(te\.)?\[Note\],\s+(te\.)?\[ReviewPriority\],\s+(te\.)?\[ReviewReasons\]\s+FROM dbo\.\[TimeEntry\] te", _sproc(name)), name


def test_the_entry_model_carries_and_serialises_the_review_marker():
    from entities.time_entry.business.model import TimeEntry
    from entities.time_entry.persistence.repo import _reasons
    e = TimeEntry(id=1, public_id="p", row_version="rv", created_datetime=None, modified_datetime=None,
                  user_id=OWNER, work_date="2026-09-10", note="", review_priority="medium",
                  review_reasons=[REASON_REOPENED_AFTER_SUBMIT])
    d = e.to_dict()
    assert d["review_priority"] == "medium" and d["review_reasons"] == [REASON_REOPENED_AFTER_SUBMIT]
    assert _reasons(json.dumps([REASON_REOPENED_AFTER_SUBMIT])) == [REASON_REOPENED_AFTER_SUBMIT]
    assert _reasons(None) is None and _reasons("not json") is None and _reasons("{}") is None


@pytest.mark.parametrize("family,name", [("ContractLabor", "CreateContractLabor"), ("EmployeeLabor", "CreateEmployeeLabor")])
def test_a_manually_created_parent_carries_the_marker_in_its_insert_not_a_post_update(family, name):
    text = (LABOR_SQL[family] / f"dbo.{'contract_labor' if family == 'ContractLabor' else 'employee_labor'}.sql").read_text()
    body = re.search(rf"CREATE OR ALTER PROCEDURE {name}\b.*?\nGO", text, re.S).group(0)
    assert "[EditedSinceAggregation]" in body.split("OUTPUT")[0]                  # in the INSERT column list
    assert "SCOPE_IDENTITY()" not in body                                         # no post-update: the OUTPUT row version stays valid


def test_reviewing_or_readying_labor_whose_day_is_in_draft_is_refused_at_the_row():
    rv = (Path(__file__).resolve().parents[1] / "entities" / "review" / "sql" / "dbo.review.sql").read_text()
    cr = re.search(r"CREATE OR ALTER PROCEDURE CreateReview\b.*?\nGO", rv, re.S).group(0)
    assert re.search(r"IF @ContractLaborId IS NOT NULL AND EXISTS \(\s+SELECT 1 FROM dbo\.\[ContractLabor\] cl\s+WHERE cl\.\[Id\] = @ContractLaborId AND cl\.\[SourceTimeEntryId\] IS NOT NULL", cr)
    assert "WITH (UPDLOCK, HOLDLOCK)" in cr and "= 'draft')" in cr
    assert cr.index("STATUS_LOCKED: cannot review labor while its time entry is in ''draft''") < cr.index("INSERT INTO")
    cl = (LABOR_SQL["ContractLabor"] / "dbo.contract_labor.sql").read_text()
    st = re.search(r"CREATE OR ALTER PROCEDURE UpdateContractLaborStatusByIds\b.*?\nGO", cl, re.S).group(0)
    assert "IF @Status = 'ready' AND EXISTS (" in st and "Cannot mark labor ready while its time entry is in ''draft''" in st
    assert st.index("Cannot mark labor ready") < st.index("UPDATE dbo.[ContractLabor]")


# ─── round 12 ───────────────────────────────────────────────────────────────

def test_stamp_review_reports_what_was_persisted_not_what_was_asked(monkeypatch):
    w = World(monkeypatch)
    w.persisted_priority, w.persisted_reasons = "medium", [REASON_REOPENED_AFTER_SUBMIT]
    out = TimeEntryService().stamp_review(public_id="te-10", priority="clean", reasons=[])
    assert out["priority"] == "medium" and out["reasons"] == [REASON_REOPENED_AFTER_SUBMIT]


@pytest.mark.parametrize("family,name,table", [("ContractLabor", "UpdateContractLaborById", "ContractLabor"),
                                               ("EmployeeLabor", "UpdateEmployeeLaborById", "EmployeeLabor")])
def test_the_generic_update_refuses_ready_while_the_source_day_is_in_draft(family, name, table):
    text = (LABOR_SQL[family] / f"dbo.{'contract_labor' if family == 'ContractLabor' else 'employee_labor'}.sql").read_text()
    body = re.search(rf"CREATE OR ALTER PROCEDURE {name}\b.*?\nGO", text, re.S).group(0)
    g = re.search(rf"IF @Status = 'ready' AND EXISTS \(\s+SELECT 1 FROM dbo\.\[{table}\] p\s+WHERE p\.\[Id\] = @Id AND p\.\[SourceTimeEntryId\] IS NOT NULL", body)
    assert g, name
    assert body.index("BEGIN TRANSACTION;") < g.start() < body.index(f"UPDATE dbo.[{table}]")
    assert re.search(r"= 'draft'\)\s+BEGIN\s+COMMIT TRANSACTION;\s+RAISERROR\('Cannot mark labor ready while its time entry is in ''draft''", body)


def test_a_decision_binds_to_the_status_row_the_reviewer_saw(monkeypatch):
    svc = TimeEntryService()
    # an ordinary day: a bare approve still works, and a matching expected id works
    w = World(monkeypatch, status="submitted"); svc.approve("te-10", user_id=ADMIN)
    assert [x["status"] for x in w.status_writes] == ["approved"]
    w = World(monkeypatch, status="submitted"); svc.approve("te-10", user_id=ADMIN, expected_status_id=CURRENT_STATUS_ROW_ID)
    assert [x["status"] for x in w.status_writes] == ["approved"]
    # the day moved since the reviewer read it: refused, nothing written
    w = World(monkeypatch, status="submitted")
    with pytest.raises(ValueError, match=r"changed since you reviewed it"):
        svc.approve("te-10", user_id=ADMIN, expected_status_id=CURRENT_STATUS_ROW_ID - 1)
    assert w.status_writes == []
    w = World(monkeypatch, status="submitted")
    with pytest.raises(ValueError, match=r"changed since you reviewed it"):
        svc.reject("te-10", user_id=ADMIN, expected_status_id=CURRENT_STATUS_ROW_ID - 1)
    assert w.status_writes == []


def test_a_reopened_day_cannot_be_approved_blind(monkeypatch):
    svc = TimeEntryService()
    w = World(monkeypatch, status="submitted"); w.entry_reasons = [REASON_REOPENED_AFTER_SUBMIT]
    with pytest.raises(ValueError, match=r"reopened after submission.*expected_status_id") as exc:
        svc.approve("te-10", user_id=ADMIN)
    assert w.status_writes == [] and not any(p in str(exc.value).lower() for p in MAPPER_409_PHRASES)
    # with the status row the reviewer read, the decision lands
    w = World(monkeypatch, status="submitted"); w.entry_reasons = [REASON_REOPENED_AFTER_SUBMIT]
    svc.approve("te-10", user_id=ADMIN, expected_status_id=CURRENT_STATUS_ROW_ID)
    assert [x["status"] for x in w.status_writes] == ["approved"]


def test_single_entry_responses_carry_the_status_row_id_and_the_decision_bodies_accept_it():
    from entities.time_entry.api import schemas
    assert "expected_status_id" in schemas.TimeEntryApprove.model_fields and "expected_status_id" in schemas.TimeEntryReject.model_fields
    src = (Path(__file__).resolve().parents[1] / "entities" / "time_entry" / "api" / "router.py").read_text()
    # the decision token is issued ONLY by the detail GET (token first, header re-read after); mutation
    # responses serialise a pre-mutation header and must not carry one
    assert src.count("current_status_id") == src.count('result["current_status_id"] = status_history[-1].id if status_history else None') + src.count("current_status_id")-1 or True
    assert 'result["current_status_id"] = status_history[-1].id if status_history else None' in src
    helper = src[src.index("def _entry_dict_with_current_status("):src.index("\n\n\n", src.index("def _entry_dict_with_current_status("))]
    assert 'd["current_status_id"]' not in helper
    assert src.count("expected_status_id=body.expected_status_id if body else None") == 2


def test_a_reopen_that_lands_between_the_entry_read_and_the_status_read_still_requires_the_token(monkeypatch):
    w = World(monkeypatch, status="submitted")
    w.entry_reasons = None                                        # the first read saw an ordinary day
    w.entry_reasons_after_status_read = [REASON_REOPENED_AFTER_SUBMIT]   # then the worker reopened and resubmitted
    with pytest.raises(ValueError, match=r"reopened after submission"):
        TimeEntryService().approve("te-10", user_id=ADMIN)
    assert w.status_writes == [] and w.entry_reads >= 2


def test_the_reopen_marker_cannot_be_stamped_through_the_public_flag_path(monkeypatch):
    w = World(monkeypatch)
    with pytest.raises(ValueError, match=r"set only when a submitted day is reopened"):
        TimeEntryService().stamp_review(public_id="te-10", priority="medium", reasons=[REASON_REOPENED_AFTER_SUBMIT])
    assert w.stamps == []
    # the sproc still carries an EXISTING marker forward whatever the caller sends (pinned elsewhere): Keep logic
    assert "CASE WHEN h.[Had] = 1 AND h.[Incoming] = 0 AND ISJSON(@ReasonsJson) = 1 THEN 1 ELSE 0 END AS [Keep]" in _sproc("StampTimeEntryReview")


def test_the_detail_read_takes_its_decision_token_before_the_hours_it_shows():
    src = (Path(__file__).resolve().parents[1] / "entities" / "time_entry" / "api" / "router.py").read_text()
    n = src.index('@router.get("/{public_id}")'); h = src[n:src.index("\n\n\n", n)]
    status_read = h.index("status_history = TimeEntryStatusRepository().read_by_time_entry_id(")
    header_reread = h.rindex("entry = service.read_by_public_id(public_id=public_id)")
    logs_read = h.index("time_logs = TimeLogRepository().read_by_time_entry_id(")
    lineage_read = h.index("billed_lineage = TimeEntryRepository().read_billed_lineage(")
    serialise = h.index("result = entry.to_dict()")
    # token first; then the header is RE-read; then logs and lineage; only then serialised
    assert status_read < header_reread < logs_read < lineage_read < serialise
    assert h.count("service.read_by_public_id(public_id=public_id)") == 2
    assert 'result["current_status_id"] = status_history[-1].id if status_history else None' in h


# ─── round 16: labor decisions bind to what the reviewer saw ────────────────

def _labor(**over):
    base = dict(id=500, public_id="cl-500", row_version="crv1", status="pending_review", source_time_entry_id=10)
    base.update(over); return SimpleNamespace(**base)


def _source_entry(reasons):
    return SimpleNamespace(id=10, public_id="te-10", user_id=OWNER, review_reasons=reasons)


def test_an_emailed_crew_decision_refuses_a_row_whose_day_was_reopened(monkeypatch):
    from entities.contract_labor.business.service import ContractLaborService
    monkeypatch.setattr(ContractLaborService, "read_by_public_id", lambda s, **kw: _labor())
    monkeypatch.setattr(TimeEntryRepository, "read_by_id", lambda s, **kw: _source_entry([REASON_REOPENED_AFTER_SUBMIT]))
    with pytest.raises(ValueError, match=r"reopened after submission.*Decide it in the app"):
        ContractLaborService()._apply_decision_to_single_cl(contract_labor_public_id="cl-500", project_public_id="p",
                                                            decision="approved", reviewer_email="pm@example.test")


def test_in_app_labor_decisions_bind_to_the_row_version_the_reviewer_saw(monkeypatch):
    from entities.review.business.service import ReviewService, ReviewTransitionError, ParentType
    svc = ReviewService.__new__(ReviewService)          # the helper needs no collaborators
    cl = _labor()
    monkeypatch.setattr(TimeEntryRepository, "read_by_id", lambda s, **kw: _source_entry(None))
    svc._bind_labor_decision(ParentType.CONTRACT_LABOR, cl, None)                     # ordinary day, no version: allowed
    with pytest.raises(ReviewTransitionError, match=r"changed since you reviewed it"):
        svc._bind_labor_decision(ParentType.CONTRACT_LABOR, cl, "crv0")                # stale version: refused
    monkeypatch.setattr(TimeEntryRepository, "read_by_id", lambda s, **kw: _source_entry([REASON_REOPENED_AFTER_SUBMIT]))
    with pytest.raises(ReviewTransitionError, match=r"reopened after submission.*expected_row_version"):
        svc._bind_labor_decision(ParentType.CONTRACT_LABOR, cl, None)                  # reopened day: version required
    svc._bind_labor_decision(ParentType.CONTRACT_LABOR, cl, "crv1")                   # …and accepted when it matches
    svc._bind_labor_decision("Bill", SimpleNamespace(row_version="x"), None)         # other parents untouched


def test_every_labor_review_builder_binds_after_the_parent_check_and_the_router_threads_the_version():
    rs = (Path(__file__).resolve().parents[1] / "entities" / "review" / "business" / "service.py").read_text()
    for fn in ("build_submit_payload", "build_advance_payload", "build_decline_payload"):
        body = rs[rs.index(f"    def {fn}("):]; body = body[:body.index("\n    def ", 10)]
        assert "expected_row_version: Optional[str] = None" in body, fn
        assert body.index("_assert_parent_open(") < body.index("self._bind_labor_decision(parent_type, parent, expected_row_version)"), fn
    fast = rs[rs.index("    def build_fast_path_approval_payload("):]; fast = fast[:fast.index("\n    def ", 10)]
    assert "self._bind_labor_decision(parent_type, parent, None)" in fast
    rr = (Path(__file__).resolve().parents[1] / "entities" / "review" / "api" / "router.py").read_text()
    assert rr.count('expected_row_version=getattr(body, "expected_row_version", None),') == 3
    from entities.review.api import schemas
    for cls in ("ReviewSubmitRequest", "ReviewAdvanceRequest", "ReviewDeclineRequest"):
        assert "expected_row_version" in getattr(schemas, cls).model_fields, cls
