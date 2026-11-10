"""U-551b-f1-f5: the stranded `confirmed` sweeper (sprocs, repo, service, route).

Every test names the mutant it kills. Nothing opens a DB/HTTP connection: the repo, the suggestion lock
and the outbox are mocked (get_connection is blocked by conftest)."""
import inspect
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.coding_suggestion.persistence import repo as repo_module
from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
from integrations.intuit.qbo.base.errors import CodingSuggestionBusyError
from shared.api import admin
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
LOCK = "entities.coding_suggestion.business.service.coding_suggestion_lock"
P1, P2, P3 = ("11111111-1111-1111-1111-11111111111%d" % i for i in (1, 2, 3))


# ---- sproc text pins -------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ReadStrandedConfirmedCodingSuggestions", "ReadCodingSuggestionRecoveryCandidates"])
def test_new_sprocs_exist_nocount_and_go_terminated(name):
    # kills: a sproc dropped/renamed (sproc_body asserts presence), SET NOCOUNT ON dropped (row-count
    # result sets break pyodbc fetchall), or the batch left un-GO-terminated (swallows the next sproc)
    body = sproc_body(SQL, name)
    assert "SET NOCOUNT ON" in body
    assert re.search(rf"PROCEDURE\s+{name}\b.*?^GO\s*$", SQL.read_text(), re.DOTALL | re.MULTILINE)
    # kills: the GO after this sproc deleted (the next CREATE OR ALTER PROCEDURE is swallowed into the body)
    assert "CREATE OR ALTER PROCEDURE" not in body


def test_stranded_sproc_live_outbox_statuses_exactly_pending_failed_in_progress_not_done():
    # kills: `done` counted as live (a stranded row with a finished outbox row never re-enqueues) or a
    # live status dropped (a pending row would be double-enqueued)
    body = strip_sql_comments(sproc_body(SQL, "ReadStrandedConfirmedCodingSuggestions"))
    match = re.search(r"NOT EXISTS\s*\(.*?o\.\[Status\]\s+IN\s*\(([^)]*)\)", body, re.DOTALL)
    assert match is not None
    assert sorted(re.findall(r"N'(\w+)'", match.group(1))) == ["failed", "in_progress", "pending"]
    assert "N'confirmed'" in body and "[HasDeadLetter]" in body
    assert "N'dead_letter'" in body
    assert "SECOND, -@OlderThanSeconds" in body
    # kills: the anti-join losing its Kind / EntityType / EntityPublicId predicate (any other row, entity or
    # kind would then mask or fake a live outbox row)
    anti = re.search(r"NOT EXISTS\s*\((.*?)\)\s*ORDER BY", body, re.DOTALL).group(1)
    assert "o.[Kind] = N'recode_purchase_line'" in anti
    # U-551b-f1-f5 fix: CodingSuggestion is the only entity type enqueued (U-652 retired ExpenseCodingItem;
    # tests/test_u652_coding_suggestion_sql_static.py forbids the literal in scoped SQL) — same vocabulary as the service guard
    types = re.search(r"o\.\[EntityType\]\s*=\s*(N'\w+')", anti).group(1)
    assert re.findall(r"N'(\w+)'", types) == ["CodingSuggestion"]
    assert "o.[EntityPublicId] = cs.[PublicId]" in anti
    # kills: the HasDeadLetter arm widened/changed to another status (a failed row would block the sweeper)
    dead = re.search(r"CASE WHEN EXISTS\s*\((.*?)\)\s*THEN 1", body, re.DOTALL).group(1)
    assert re.findall(r"d\.\[Status\]\s*(?:=|IN)\s*\(?\s*N'(\w+)'", dead) == ["dead_letter"]
    assert set(re.findall(r"N'(\w+)'", dead)) - {"recode_purchase_line", "CodingSuggestion"} == {"dead_letter"}


def test_recovery_sproc_covers_error_repoint_and_written_without_record():
    # kills: the `error` arm widened past complete_repoint (first-write errors are the cockpit's), the
    # written arm losing its record-null predicates, or @RecordRequiredSince ignored
    body = strip_sql_comments(sproc_body(SQL, "ReadCodingSuggestionRecoveryCandidates"))
    assert "N'error_repoint'" in body and "N'written_without_record'" in body
    assert "[ConfirmationSource] = N'complete_repoint'" in body
    assert "[WrittenSyncToken] IS NULL" in body and "[WrittenLinesJson] IS NULL" in body
    assert "@RecordRequiredSince IS NULL OR cs.[WrittenAt] >= @RecordRequiredSince" in body


def test_confirmation_guard_admits_error_only_in_the_seq_given_branch():
    # kills: 'error' added to the unfenced branch (any confirm could resurrect an error row) or left out
    # of the fenced branch (the admin redrive misses -> never re-records)
    body = strip_sql_comments(sproc_body(SQL, "RecordCodingSuggestionConfirmation"))
    where = body[body.index("WHERE cs.[PublicId] = @PublicId"):]
    unfenced, fenced = where.split("OR (", 1)
    assert "@ExpectedConfirmationSeq IS NULL" in unfenced and "error" not in unfenced
    assert re.search(r"IN \(N'written', N'confirmed', N'enqueued', N'error'\)", fenced)
    assert "[ConfirmationSeq] = @ExpectedConfirmationSeq" in fenced


# ---- repo ------------------------------------------------------------------------------------------

def _repo_with_rows(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    return ctx


def test_repo_read_stranded_passes_params_and_maps_rows():
    # kills: OlderThanSeconds/Limit not forwarded (always the sproc default), HasDeadLetter dropped
    row = SimpleNamespace(PublicId=P1, RealmId="r", ConfirmationSeq=3, ConfirmationSource="confirm_endpoint",
                          ConfirmedAt="2026-11-08 00:00:00.000", HasDeadLetter=1)
    with patch.object(repo_module, "get_connection", return_value=_repo_with_rows([row])), \
            patch.object(repo_module, "call_procedure") as call:
        out = CodingSuggestionRepository().read_stranded_confirmed(older_than_seconds=900, limit=7)
    assert call.call_args.kwargs["name"] == "ReadStrandedConfirmedCodingSuggestions"
    assert call.call_args.kwargs["params"] == {"OlderThanSeconds": 900, "Limit": 7}
    assert out == [{"public_id": P1, "realm_id": "r", "confirmation_seq": 3,
                    "confirmation_source": "confirm_endpoint", "confirmed_at": "2026-11-08 00:00:00.000",
                    "has_dead_letter": True}]


def test_repo_read_recovery_passes_params_and_maps_rows():
    # kills: RecordRequiredSince/Limit not forwarded (legacy rows flood the report), Category dropped
    row = SimpleNamespace(PublicId=P2, RealmId="r", ConfirmationSeq=2, Category="error_repoint", WriteError="boom")
    with patch.object(repo_module, "get_connection", return_value=_repo_with_rows([row])), \
            patch.object(repo_module, "call_procedure") as call:
        out = CodingSuggestionRepository().read_recovery_candidates(record_required_since="SINCE", limit=5)
    assert call.call_args.kwargs["name"] == "ReadCodingSuggestionRecoveryCandidates"
    assert call.call_args.kwargs["params"] == {"RecordRequiredSince": "SINCE", "Limit": 5, "EnqueuedOlderThanSeconds": 300}
    assert out[0]["category"] == "error_repoint" and out[0]["write_error"] == "boom"


# ---- service ---------------------------------------------------------------------------------------

def _cand(pid, seq=4, dead=False):
    return {"public_id": pid, "realm_id": "realm-1", "confirmation_seq": seq,
            "confirmation_source": "confirm_endpoint", "confirmed_at": "x", "has_dead_letter": dead}


def _item(status="confirmed", seq=4):
    return SimpleNamespace(status=status, confirmation_seq=seq, realm_id="realm-1")


def _svc(cands, items=None, recovery=()):
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_stranded_confirmed.return_value = cands
    svc.repo.read_recovery_candidates.return_value = list(recovery)
    svc.repo.heal_expense_ids.return_value = 0  # U-652d5: the real repo returns an int (JSON-safe)
    items = items if items is not None else {c["public_id"]: _item(seq=c["confirmation_seq"]) for c in cands}
    svc.read_by_public_id = MagicMock(side_effect=lambda pid: items[pid])
    svc._enqueue_recode_and_mark = MagicMock(return_value=True)
    svc.record_confirmation = MagicMock()
    svc.mark_error = MagicMock()
    return svc


@contextmanager
def _recording_lock(calls, acquired=True):
    @contextmanager
    def lock(public_id, *, timeout_ms):
        calls.append((public_id, timeout_ms))
        yield acquired
    with patch(LOCK, lock):
        yield


@patch(GATE, return_value="ALLOW_QBO_WRITES off")
def test_gate_off_apply_skips_with_reason_and_zero_writes(_gate):
    # kills: the sweeper bypassing recode_write_gate_reason (would enqueue while writes are disabled)
    locks = []
    svc = _svc([_cand(P1)])
    with _recording_lock(locks):
        out = svc.sweep_stranded_confirmed(dry_run=False)
    assert out["status"] == "skipped" and out["reason"] == "ALLOW_QBO_WRITES off"
    svc._enqueue_recode_and_mark.assert_not_called()
    assert locks == []
    svc.record_confirmation.assert_not_called()


@patch(GATE, return_value="ALLOW_QBO_WRITES off")
def test_gate_off_dry_run_still_reports_gate_and_lists_candidates(_gate):
    # kills: dry-run hiding the gate state / candidates when the gate is off
    svc = _svc([_cand(P1)])
    out = svc.sweep_stranded_confirmed(dry_run=True)
    assert out["gate"] == "ALLOW_QBO_WRITES off"
    assert out["would_enqueue"] == [P1] and out["candidates"] == 1


@patch(GATE, return_value=None)
def test_dry_run_lists_would_enqueue_without_lock_or_writes(_gate):
    # kills: dry_run ignored (a report-only call writes) or a lock taken on a read-only pass
    locks = []
    svc = _svc([_cand(P1), _cand(P2)])
    with _recording_lock(locks):
        out = svc.sweep_stranded_confirmed()
    assert out["would_enqueue"] == [P1, P2] and out["enqueued"] == 0
    assert locks == []
    svc._enqueue_recode_and_mark.assert_not_called()
    svc.read_by_public_id.assert_not_called()


@patch(GATE, return_value=None)
def test_apply_locks_rereads_and_enqueues_with_the_rows_seq(_gate):
    # kills: enqueue without the lock or the re-read, or with a seq other than the row's. (A stale-seq\n    # mutant on the equality re-check is equivalent while the check stands; removing the re-check is\n    # killed by the moved-seq test below.)
    locks = []
    svc = _svc([_cand(P1, seq=9)])
    with _recording_lock(locks):
        out = svc.sweep_stranded_confirmed(dry_run=False)
    assert out["enqueued"] == 1 and out["status"] == "ok"
    assert locks == [(P1, 10_000)]
    svc.read_by_public_id.assert_called_once_with(P1)
    svc._enqueue_recode_and_mark.assert_called_once_with(P1, "realm-1", 9)


@patch(GATE, return_value=None)
def test_dead_letter_blocks_with_no_lock_and_no_write(_gate):
    # kills: re-enqueueing behind a dead letter (a dead-lettered write may already be in QBO)
    locks = []
    svc = _svc([_cand(P1, dead=True)])
    with _recording_lock(locks):
        out = svc.sweep_stranded_confirmed(dry_run=False)
    assert out["dead_letter_pending"] == [P1] and out["enqueued"] == 0
    assert locks == []
    svc._enqueue_recode_and_mark.assert_not_called()


@patch(GATE, return_value=None)
@pytest.mark.parametrize("reread", [_item("enqueued", 4), _item("confirmed", 5), None])
def test_reread_that_moved_is_reported_moved_and_not_enqueued(_gate, reread):
    # kills: skipping the in-lock re-check (status/seq/missing row) -> enqueue on a revision that moved
    svc = _svc([_cand(P1, seq=4)], items={P1: reread})
    out = svc.sweep_stranded_confirmed(dry_run=False)
    assert out["moved"] == [P1] and out["enqueued"] == 0
    svc._enqueue_recode_and_mark.assert_not_called()


@patch(GATE, return_value=None)
def test_busy_lock_and_busy_error_are_busy_and_the_next_row_proceeds(_gate):
    # kills: busy aborting the sweep (rows after it never retried) or being counted as an error
    svc = _svc([_cand(P1), _cand(P2), _cand(P3)])
    svc._enqueue_recode_and_mark.side_effect = [CodingSuggestionBusyError(P2), True]

    @contextmanager
    def lock(public_id, *, timeout_ms):
        yield public_id != P1   # P1: lock not acquired

    with patch(LOCK, lock):
        out = svc.sweep_stranded_confirmed(dry_run=False)
    assert out["busy"] == [P1, P2] and out["enqueued"] == 1 and out["errors"] == []


@patch(GATE, return_value=None)
def test_one_row_failure_is_recorded_and_the_next_row_still_processes(_gate):
    # kills: an exception on one row aborting the batch (or swallowing it unrecorded)
    svc = _svc([_cand(P1), _cand(P2)])
    svc._enqueue_recode_and_mark.side_effect = [RuntimeError("secret detail"), True]
    out = svc.sweep_stranded_confirmed(dry_run=False)
    assert out["errors"] == [{"id": P1, "error": "RuntimeError"}]   # class name only, no message
    assert out["enqueued"] == 1


@patch(GATE, return_value=None)
def test_recovery_lists_populated_and_error_rows_never_redriven(_gate):
    # kills: recovery dropped / record_required_since not passed through / an error row being re-enqueued
    since = object()
    rec = [{"public_id": P1, "category": "error_repoint"}, {"public_id": P2, "category": "written_without_record"}]
    svc = _svc([], recovery=rec)
    out = svc.sweep_stranded_confirmed(dry_run=False, older_than_seconds=600, limit=5, record_required_since=since)
    assert out["recovery"] == {"error_repoint": [P1], "written_without_record": [P2], "enqueued_stranded": []}
    svc.repo.read_stranded_confirmed.assert_called_once_with(older_than_seconds=600, limit=5)
    svc.repo.read_recovery_candidates.assert_called_once_with(
        record_required_since=since, limit=5, enqueued_older_than_seconds=300)
    svc._enqueue_recode_and_mark.assert_not_called()
    svc.record_confirmation.assert_not_called()


@patch(GATE, return_value=None)
def test_result_is_json_safe(_gate):
    # kills: a non-JSON value (exception object, dataclass) leaking into the _timed payload
    import json
    svc = _svc([_cand(P1), _cand(P2, dead=True)])
    svc._enqueue_recode_and_mark.side_effect = RuntimeError("x")
    json.dumps(svc.sweep_stranded_confirmed(dry_run=False))


# ---- route -----------------------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("DRAIN_SECRET", "unit-test-drain-secret")


HDR = {"X-Drain-Secret": "unit-test-drain-secret"}
URL = "/api/v1/admin/expense-coding/sweep-stranded"


def test_route_503_when_secret_unconfigured(client, monkeypatch):
    # kills: the drain-secret dependency removed from the route
    monkeypatch.setenv("DRAIN_SECRET", "")
    with patch.object(CodingSuggestionService, "sweep_stranded_confirmed") as sweep:
        assert client.post(URL, headers=HDR).status_code == 503
    sweep.assert_not_called()


def test_route_401_on_wrong_secret(client, secret):
    # kills: secret compare weakened on this route
    with patch.object(CodingSuggestionService, "sweep_stranded_confirmed") as sweep:
        assert client.post(URL, headers={"X-Drain-Secret": "nope"}).status_code == 401
    sweep.assert_not_called()


def test_route_200_envelope_passes_query_and_constant(client, secret):
    # kills: params not forwarded, apply inverted, the since constant not passed, envelope shape changed
    with patch.object(CodingSuggestionService, "sweep_stranded_confirmed", return_value={"candidates": 2}) as sweep:
        body = client.post(URL + "?apply=true&older_than_seconds=600&limit=7", headers=HDR).json()
    assert body["status"] == "ok" and body["job"] == "expense_coding.sweep_stranded"
    assert body["result"] == {"candidates": 2}
    sweep.assert_called_once_with(dry_run=False, older_than_seconds=600, limit=7,
                                  record_required_since=admin.RECORD_REQUIRED_SINCE_UTC)


def test_route_default_is_dry_run(client, secret):
    # kills: apply defaulting to True (a bare POST would write)
    with patch.object(CodingSuggestionService, "sweep_stranded_confirmed", return_value={}) as sweep:
        client.post(URL, headers=HDR)
    assert sweep.call_args.kwargs["dry_run"] is True
    assert sweep.call_args.kwargs["older_than_seconds"] == 300 and sweep.call_args.kwargs["limit"] == 100
    assert "apply: bool = Query(default=False)" in inspect.getsource(admin.sweep_stranded_coding_suggestions_router)


@pytest.mark.parametrize("query", ["older_than_seconds=59", "older_than_seconds=86401", "limit=0", "limit=501"])
def test_route_query_bounds_422(client, secret, query):
    # kills: the ge/le bounds loosened (a 0s window re-enqueues rows mid-confirm; an unbounded limit)
    with patch.object(CodingSuggestionService, "sweep_stranded_confirmed") as sweep:
        assert client.post(f"{URL}?{query}", headers=HDR).status_code == 422
    sweep.assert_not_called()


def test_route_runs_under_system_authz():
    # kills: the service call leaving system_authz() (scoped sprocs would return nothing)
    src = inspect.getsource(admin.sweep_stranded_coding_suggestions_router)
    assert "with system_authz():" in src and "Depends(_require_drain_secret)" in inspect.getsource(admin).split(
        "def sweep_stranded_coding_suggestions_router")[0].rsplit("@router.post", 1)[1]
