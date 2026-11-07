"""U-551b-f7 (folded into f2): the completion repoint probe (a) reports a failure as a skip, never the
first-write fallthrough, and (b) walks EVERY candidate, each under its own lock hold.

Every test names the mutant it kills. No DB/HTTP connection: state/record reads, the resolver, the lock
and confirm are all patched."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.business.service import ExpenseService

STATE_READ = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids"
FULL_READ = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_by_public_id"
CONFIRM = "entities.coding_suggestion.business.service.CodingSuggestionService.confirm"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
UNCODED = "entities.expense.business.service.ExpenseService._expense_has_genuine_uncoded_staged_lines"
DOMINANT = "entities.expense.business.service.ExpenseService._resolve_dominant_expense_coding_for_submit"
LOCK_TARGET = "entities.coding_suggestion.business.service.qbo_app_lock"


def _eli(qbo_id, sub=9):
    return SimpleNamespace(id=int(qbo_id[1:]), qbo_id=qbo_id, sub_cost_code_id=sub, project_id=1, amount=10, description="d")


def _svc(*line_ids):
    svc = ExpenseService(repo=MagicMock())
    svc.read_by_id = MagicMock(return_value=SimpleNamespace(id=55, qbo_id="P", realm_id="realm"))
    eli = MagicMock()
    eli.read_by_expense_id.return_value = [_eli(i) for i in line_ids]
    svc._expense_line_item_service = eli
    return svc


def _full(line_id, item="item-A", status="written"):
    return SimpleNamespace(
        status=status, written_sync_token="6", confirmation_source=None, confirmation_seq=3,
        written_lines=[{"qbo_line_id": line_id, "item_ref_value": item, "sub_cost_code_id": 5}])


def _lock(events, denied=()):
    @contextmanager
    def lock(resource, timeout_ms=15000):
        pid = resource.rsplit(":", 1)[1]
        grant = pid not in denied
        events.append(("enter" if grant else "denied", pid))
        try:
            yield grant
        finally:
            events.append(("exit", pid))
    return lock


def _complete(svc, states, fulls, *, denied=(), confirm_results=None, full_side_effect=None, state_side_effect=None):
    """fulls: {public_id: record}; confirm_results: {public_id: confirm envelope} (default enqueued)."""
    events: list = []

    def _confirm(**kw):
        events.append(("confirm", kw["public_id"]))
        return (confirm_results or {}).get(kw["public_id"], {"status": "enqueued", "enqueued": True})

    uncoded = MagicMock(return_value=False)
    confirm = MagicMock(side_effect=_confirm)
    with patch(STATE_READ, MagicMock(side_effect=state_side_effect, return_value={55: states})), \
            patch(FULL_READ, side_effect=full_side_effect or (lambda pid: fulls[pid])), \
            patch(RESOLVER, side_effect=lambda *a, **k: SimpleNamespace(value="item-B")), \
            patch(CONFIRM, confirm), patch(DOMINANT, side_effect=AssertionError("must not be called")), \
            patch(UNCODED, uncoded), patch(LOCK_TARGET, _lock(events, denied)):
        out = svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True)
    return out, events, confirm, uncoded


def _written(*ids):
    return [{"public_id": i, "status": "written"} for i in ids]


# ---- F7(a): a probe failure is a reported skip ----------------------------------------------------

def test_raising_state_read_is_a_reported_skip_never_the_first_write_path():
    # kills: the outer `except` returning None (falls through and reads "no_uncoded_staged_lines")
    out, events, confirm, uncoded = _complete(
        _svc("L1"), [], {}, state_side_effect=RuntimeError("boom"))
    assert out["success"] is True and out["enqueued"] == 0 and out["skipped"] is True
    assert out["skip_reason"] == "repoint_probe_failed" and out["message"] == "Repoint not enqueued"
    assert out["public_id"] is None  # tolerated: the skip carries no candidate
    assert out["skipped_candidates"] == []  # r3 (Codex P3): the same shape as every other envelope
    confirm.assert_not_called()
    uncoded.assert_not_called()


def test_raising_expense_read_is_a_reported_skip_never_the_first_write_path():
    # kills: None fallthrough when the expense read (outside the per-candidate loop) raises
    svc = _svc("L1")
    svc.read_by_id = MagicMock(side_effect=RuntimeError("boom"))
    out, events, confirm, uncoded = _complete(svc, _written("cs-1"), {"cs-1": _full("L1")})
    assert out["skip_reason"] == "repoint_probe_failed"
    confirm.assert_not_called()
    uncoded.assert_not_called()


@pytest.mark.parametrize("where", ["read_by_public_id", "read_by_expense_id"])
def test_raising_candidate_read_is_a_reported_skip_never_the_first_write_path(where):
    # kills: the None fallthrough for a candidate read raising (record read / line read)
    svc = _svc("L1")
    kwargs = {}
    if where == "read_by_public_id":
        kwargs["full_side_effect"] = RuntimeError("boom")
    else:
        svc._expense_line_item_service.read_by_expense_id.side_effect = RuntimeError("boom")
    out, events, confirm, uncoded = _complete(svc, _written("cs-1"), {"cs-1": _full("L1")}, **kwargs)
    assert out["success"] is True and out["enqueued"] == 0 and out["skip_reason"] == "repoint_probe_failed"
    assert out["skipped_candidates"] == [{"public_id": "cs-1", "skip_reason": "repoint_probe_failed"}]
    assert events == [("enter", "cs-1"), ("exit", "cs-1")]  # the lock hold was released on the raise
    confirm.assert_not_called()
    uncoded.assert_not_called()


# ---- F7(b): every candidate, each under its own hold ----------------------------------------------

def test_two_diverged_suggestions_are_both_confirmed_each_inside_its_own_lock_hold():
    # kills: the early return on the first diverged candidate; a confirm outside its hold
    out, events, confirm, uncoded = _complete(
        _svc("L1", "L2"), _written("cs-1", "cs-2"), {"cs-1": _full("L1"), "cs-2": _full("L2")})
    assert events == [("enter", "cs-1"), ("confirm", "cs-1"), ("exit", "cs-1"),
                      ("enter", "cs-2"), ("confirm", "cs-2"), ("exit", "cs-2")]
    assert out["success"] is True and out["enqueued"] == 2 and out["repoint"] is True
    assert out["message"] == "Purchase line repoint enqueued"
    assert out["lines"] == ["L1", "L2"] and out["errors"] == [] and out["skipped"] is False
    assert out["skipped_candidates"] == []
    assert all(c.kwargs["repoint"] is True and c.kwargs["lock_held"] is True for c in confirm.call_args_list)
    uncoded.assert_not_called()


@pytest.mark.parametrize("skip_first", [True, False])
def test_one_diverged_one_skip_enqueues_one_and_lists_the_skip(skip_first):
    # kills: returning the skip (or the enqueue) of the first candidate only
    ids = ("cs-skip", "cs-ok") if skip_first else ("cs-ok", "cs-skip")
    fulls = {"cs-ok": _full("L1"), "cs-skip": _full("L9")}  # L9 has no local line -> repoint_line_missing_local
    out, events, confirm, uncoded = _complete(_svc("L1"), _written(*ids), fulls)
    assert out["enqueued"] == 1 and out["repoint"] is True and out["lines"] == ["L1"]
    assert out["skipped"] is False and out["skipped_candidates"] == [{"public_id": "cs-skip", "skip_reason": "repoint_line_missing_local"}]
    assert [c.kwargs["public_id"] for c in confirm.call_args_list] == ["cs-ok"]
    uncoded.assert_not_called()


@pytest.mark.parametrize("busy_first", [True, False])
def test_a_busy_lock_skips_only_that_candidate(busy_first):
    # kills: a busy lock aborting the whole probe
    ids = ("cs-busy", "cs-ok") if busy_first else ("cs-ok", "cs-busy")
    out, events, confirm, uncoded = _complete(
        _svc("L1"), _written(*ids), {"cs-ok": _full("L1"), "cs-busy": _full("L1")}, denied={"cs-busy"})
    assert out["enqueued"] == 1 and out["skipped"] is False and out["skipped_candidates"] == [{"public_id": "cs-busy", "skip_reason": "busy"}]
    assert ("confirm", "cs-busy") not in events
    assert [c.kwargs["public_id"] for c in confirm.call_args_list] == ["cs-ok"]
    uncoded.assert_not_called()


def test_a_candidates_confirm_skip_is_listed_and_the_other_still_enqueues():
    # kills: treating a skip envelope from confirm as an enqueue (counted, lines merged)
    out, events, confirm, uncoded = _complete(
        _svc("L1", "L2"), _written("cs-1", "cs-2"), {"cs-1": _full("L1"), "cs-2": _full("L2")},
        confirm_results={"cs-1": {"status": "repoint_revision_lost"}})
    assert out["enqueued"] == 1 and out["lines"] == ["L2"]
    assert out["skipped"] is False and out["skipped_candidates"] == [{"public_id": "cs-1", "skip_reason": "repoint_revision_lost"}]


def test_one_candidates_probe_failure_does_not_hide_another_candidates_enqueue():
    # kills: an exception in a later/earlier candidate turning an enqueued completion into a skip
    def full(pid):
        if pid == "cs-1":
            raise RuntimeError("boom")
        return _full("L2")

    out, events, confirm, uncoded = _complete(
        _svc("L1", "L2"), _written("cs-1", "cs-2"), {}, full_side_effect=full)
    assert out["enqueued"] == 1 and out["lines"] == ["L2"]
    assert out["skipped"] is False and out["skipped_candidates"] == [{"public_id": "cs-1", "skip_reason": "repoint_probe_failed"}]
    uncoded.assert_not_called()


def test_no_enqueue_and_several_skips_report_the_first_reason_and_list_them_all():
    # kills: reporting the last skip / dropping the others
    out, events, confirm, uncoded = _complete(
        _svc("L1"), _written("cs-1", "cs-2"), {"cs-2": _full("L9")}, denied={"cs-1"})
    assert out["success"] is True and out["enqueued"] == 0 and out["skipped"] is True
    assert out["skip_reason"] == "busy" and out["message"] == "Repoint not enqueued"
    assert out["skipped_candidates"] == [{"public_id": "cs-1", "skip_reason": "busy"},
                                         {"public_id": "cs-2", "skip_reason": "repoint_line_missing_local"}]
    confirm.assert_not_called()
    uncoded.assert_not_called()


def test_no_candidate_diverged_falls_through_to_the_first_write_path_as_before():
    # kills: returning a non-None envelope when nothing diverges (would suppress the first-write path)
    out, events, confirm, uncoded = _complete(
        _svc("L1", "L2"), _written("cs-1", "cs-2"),
        {"cs-1": _full("L1", item="item-B"), "cs-2": _full("L2", item="item-B")})
    confirm.assert_not_called()
    uncoded.assert_called_once()
    assert out["skip_reason"] == "no_uncoded_staged_lines"
    assert events == [("enter", "cs-1"), ("exit", "cs-1"), ("enter", "cs-2"), ("exit", "cs-2")]


# ---- F3: a malformed written record is a reported skip, a missing one is a legacy row -------------

def _full_with_json(line_id, written_lines_json, written_lines):
    full = _full(line_id)
    full.written_lines_json = written_lines_json
    full.written_lines = written_lines  # the model property fails closed to [] for unparseable JSON
    return full


def test_malformed_written_record_is_a_reported_skip_never_the_first_write_path():
    # kills: "malformed treated as legacy" (no divergence -> None -> first-write -> no_uncoded_staged_lines)
    out, events, confirm, uncoded = _complete(
        _svc("L1"), _written("cs-1"), {"cs-1": _full_with_json("L1", "{not json", [])})
    assert out["success"] is True and out["enqueued"] == 0 and out["skipped"] is True
    assert out["skip_reason"] == "repoint_written_record_malformed"
    assert out["skipped_candidates"] == [{"public_id": "cs-1", "skip_reason": "repoint_written_record_malformed"}]
    confirm.assert_not_called()
    uncoded.assert_not_called()  # the first-write path was NOT taken


@pytest.mark.parametrize("empty", [None, "", "[]", " [ ] "])
def test_missing_or_valid_empty_written_record_keeps_the_first_write_path(empty):
    # kills: "legacy treated as malformed" (a pre-U-551b-f1 ``written`` row would report a skip forever) and
    # (r3, Codex P2) "a VALID empty record treated as malformed" - [] is an explicit empty record, not corruption
    out, events, confirm, uncoded = _complete(
        _svc("L1"), _written("cs-1"), {"cs-1": _full_with_json("L1", empty, [])})
    confirm.assert_not_called()
    uncoded.assert_called_once()
    assert out["skip_reason"] == "no_uncoded_staged_lines"


@pytest.mark.parametrize("bad", ['{"a": 1}', "null", "42"])
def test_a_written_record_that_is_not_a_list_is_malformed(bad):
    # kills (r3): a malformed check that only looks at parse failure (a non-list parses fine)
    out, events, confirm, uncoded = _complete(
        _svc("L1"), _written("cs-1"), {"cs-1": _full_with_json("L1", bad, [])})
    assert out["skip_reason"] == "repoint_written_record_malformed"
    confirm.assert_not_called()
    uncoded.assert_not_called()


def test_a_pathologically_nested_written_record_is_malformed_not_a_probe_failure():
    # kills (r3, Codex P3): RecursionError from json.loads escaping the predicate into the per-candidate
    # catch - fail-closed either way, but the reason must name the record, not the probe
    deep = "[" * 100000 + "]" * 100000
    out, events, confirm, uncoded = _complete(
        _svc("L1"), _written("cs-1"), {"cs-1": _full_with_json("L1", deep, [])})
    assert out["skip_reason"] == "repoint_written_record_malformed"
    confirm.assert_not_called()
    uncoded.assert_not_called()


def test_probe_reports_the_malformed_record_per_candidate_not_for_the_whole_completion():
    # kills: the malformed check aborting the loop (another candidate's enqueue must still land)
    out, events, confirm, uncoded = _complete(
        _svc("L1", "L2"), _written("cs-bad", "cs-ok"),
        {"cs-bad": _full_with_json("L1", "[1]", []), "cs-ok": _full("L2")})
    assert out["enqueued"] == 1 and out["lines"] == ["L2"] and out["skipped"] is False
    assert out["skipped_candidates"] == [{"public_id": "cs-bad", "skip_reason": "repoint_written_record_malformed"}]


# ---- F6 / F7: envelope shape and log-once ---------------------------------------------------------

def test_both_envelopes_carry_skipped_as_a_bool_and_the_list_under_skipped_candidates():
    # kills: ``skipped`` a list on the enqueued envelope (a truthiness consumer reads 1 enqueued + 1 skipped as
    # skipped) / dropping ``skipped_candidates`` from either envelope
    enq, _, _, _ = _complete(
        _svc("L1"), _written("cs-ok", "cs-busy"), {"cs-ok": _full("L1"), "cs-busy": _full("L1")}, denied={"cs-busy"})
    assert enq["enqueued"] == 1 and enq["skipped"] is False
    assert enq["skipped_candidates"] == [{"public_id": "cs-busy", "skip_reason": "busy"}]
    skip, _, _, _ = _complete(_svc("L1"), _written("cs-busy"), {"cs-busy": _full("L1")}, denied={"cs-busy"})
    assert skip["enqueued"] == 0 and skip["skipped"] is True
    assert skip["skipped_candidates"] == [{"public_id": "cs-busy", "skip_reason": "busy"}]


LOG = "entities.expense.business.service.ExpenseService._log_recode_enqueue_skipped"


def _logged(svc, states, fulls, **kw):
    with patch(LOG) as log:
        out, *_ = _complete(svc, states, fulls, **kw)
    return out, [c.kwargs["skip_reason"] for c in log.call_args_list]


def test_each_skipped_candidate_is_logged_exactly_once_when_another_enqueued():
    # kills: the double log (once inside the skip envelope, again in the enqueued-branch loop)
    out, reasons = _logged(
        _svc("L1", "L2", "L3"), _written("cs-1", "cs-busy", "cs-ok"),
        {"cs-1": _full("L1"), "cs-busy": _full("L2"), "cs-ok": _full("L3")}, denied={"cs-busy"},
        confirm_results={"cs-1": {"status": "repoint_revision_lost"}})
    assert out["enqueued"] == 1
    assert sorted(reasons) == ["busy", "repoint_revision_lost"]


def test_every_skipped_candidate_is_logged_once_in_the_skip_only_envelope():
    # kills: logging only ``skipped[0]`` (the others silent) and the skip envelope logging a second time
    out, reasons = _logged(
        _svc("L1"), _written("cs-1", "cs-2", "cs-3"),
        {"cs-2": _full("L9"), "cs-3": _full("L1")}, denied={"cs-1"},
        confirm_results={"cs-3": {"status": "repoint_revision_lost"}})
    assert out["enqueued"] == 0 and out["skipped"] is True and out["skip_reason"] == "busy"
    assert sorted(reasons) == ["busy", "repoint_line_missing_local", "repoint_revision_lost"]
