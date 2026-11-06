"""U-551b-f1 bite 2: completion re-confirms a repoint when local coding diverged from what was written."""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
from entities.expense.business.service import ExpenseService

STATE_READ = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids"
FULL_READ = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_by_public_id"
CONFIRM = "entities.coding_suggestion.business.service.CodingSuggestionService.confirm"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
UNCODED = "entities.expense.business.service.ExpenseService._expense_has_genuine_uncoded_staged_lines"
DOMINANT = "entities.expense.business.service.ExpenseService._resolve_dominant_expense_coding_for_submit"
SUBMIT = "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit"

WRITTEN = [{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5}]


def _eli(qbo_id, sub, *, project_id=1, amount=10, description="d"):
    return SimpleNamespace(id=int("".join(c for c in qbo_id if c.isdigit())), qbo_id=qbo_id, sub_cost_code_id=sub, project_id=project_id,
                           amount=amount, description=description)


def _svc():
    svc = ExpenseService(repo=MagicMock())
    svc.read_by_id = MagicMock(return_value=SimpleNamespace(id=55, qbo_id="P", realm_id="realm"))
    eli = MagicMock()
    eli.read_by_expense_id.return_value = [_eli("L1", 9, project_id=1, amount=10)]
    svc._expense_line_item_service = eli
    return svc


def _probe(svc, expense=None):
    """The per-candidate compute (the caller holds the suggestion lock in production)."""
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    return svc._repoint_revision_for_candidate(
        expense_id=55, public_id="cs-1", expense=expense or SimpleNamespace(realm_id="realm"),
        repo=CodingSuggestionRepository())


def _full(status="written", lines=WRITTEN, source=None):
    return SimpleNamespace(status=status, written_sync_token="6", written_lines=lines, confirmation_source=source,
                           confirmation_seq=3)


def _run(svc, *, state_status="written", full=None, item_value="item-B", confirm=None,
         state_side_effect=None, qbo_linked=True, uncoded=False, resolver=None):
    state = {"public_id": "cs-1", "status": state_status}
    confirm_mock = MagicMock(return_value=confirm or {"status": "enqueued", "enqueued": True})
    uncoded_mock = MagicMock(return_value=uncoded)
    read_state = MagicMock(side_effect=state_side_effect, return_value={55: [state]})
    with patch(STATE_READ, read_state), \
            patch(FULL_READ, return_value=full or _full(status=state_status)), \
            patch(RESOLVER, side_effect=resolver or (lambda *a, **k: SimpleNamespace(value=item_value))), \
            patch(CONFIRM, confirm_mock), patch(DOMINANT, side_effect=AssertionError("must not be called")), \
            patch(UNCODED, uncoded_mock):
        out = svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=qbo_linked)
    return out, confirm_mock, uncoded_mock, read_state


def test_diverged_line_confirms_repoint():
    out, confirm, uncoded, _ = _run(_svc())
    confirm.assert_called_once()
    kw = confirm.call_args.kwargs
    assert kw["repoint"] is True and kw["public_id"] == "cs-1"
    assert kw["project_id"] == 1 and kw["sub_cost_code_id"] == 9 and kw["was_overridden"] is True
    assert kw["description"] == "d" and kw["confirmed_mixed_codes"] is False
    assert out["success"] is True and out["enqueued"] == 1 and out["repoint"] is True
    # R3-C: the targets COMPLETION approved travel with the confirmation
    assert kw["repoint_targets"] == [{"qbo_line_id": "L1", "sub_cost_code_id": 9, "project_id": 1,
                                      "description": "d", "repoint_from_item_ref_value": "item-A"}]
    assert kw["repoint_targets_token"] == "6"  # R4-B: the snapshot the targets were computed against
    assert kw["expected_confirmation_seq"] == 3  # M2: the revision the probe READ travels to confirm
    uncoded.assert_not_called()


def test_same_item_falls_through_to_uncoded_probe():
    out, confirm, uncoded, _ = _run(_svc(), item_value="item-A")
    confirm.assert_not_called()
    uncoded.assert_called_once()
    assert out["skip_reason"] == "no_uncoded_staged_lines"


def test_not_written_suggestion_falls_through():
    out, confirm, uncoded, _ = _run(_svc(), state_status="enqueued")
    confirm.assert_not_called()
    uncoded.assert_called_once()
    assert out["skip_reason"] == "no_uncoded_staged_lines"


def test_confirm_not_written_is_a_skip_but_success():
    out, confirm, uncoded, _ = _run(_svc(), confirm={"status": "not_written"})
    assert out["success"] is True and out["enqueued"] == 0
    assert out["skip_reason"] == "not_written"
    uncoded.assert_not_called()


def test_probe_failure_warns_and_completion_continues(caplog):
    with caplog.at_level(logging.WARNING):
        out, confirm, uncoded, _ = _run(_svc(), state_side_effect=RuntimeError("boom"))
    confirm.assert_not_called()
    assert out["success"] is True
    msgs = [r.getMessage() for r in caplog.records]
    assert any("expense.complete.repoint_probe_failed expense_id=55" in m for m in msgs)


def test_not_qbo_linked_reads_nothing():
    out, confirm, uncoded, read_state = _run(_svc(), qbo_linked=False)
    read_state.assert_not_called()
    confirm.assert_not_called()
    assert out["skip_reason"] == "no_uncoded_staged_lines"


# ---- R2-5: document-level coding comes from the LOCAL lines, never the staging query ----------

def _two_line_svc(l1, l2):
    svc = _svc()
    svc._expense_line_item_service.read_by_expense_id.return_value = [l1, l2]
    return svc


TWO_WRITTEN = [{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5},
               {"qbo_line_id": "L2", "item_ref_value": "item-A", "sub_cost_code_id": 5}]


def test_mixed_codes_flag_set_and_largest_diverged_line_wins():
    svc = _two_line_svc(_eli("L1", 9, project_id=1, amount=10, description="small"),
                        _eli("L2", 8, project_id=2, amount=-100, description="big"))
    out, confirm, _, _ = _run(svc, full=_full(lines=TWO_WRITTEN))
    kw = confirm.call_args.kwargs
    assert kw["project_id"] == 2 and kw["sub_cost_code_id"] == 8 and kw["description"] == "big"
    assert kw["confirmed_mixed_codes"] is True
    assert out["enqueued"] == 1


def test_tie_on_amount_picks_lowest_numeric_line_id():
    svc = _two_line_svc(_eli("10", 9, project_id=1, amount=50, description="ten"),
                        _eli("2", 9, project_id=1, amount=-50, description="two"))
    written = [{"qbo_line_id": "10", "item_ref_value": "item-A", "sub_cost_code_id": 5},
               {"qbo_line_id": "2", "item_ref_value": "item-A", "sub_cost_code_id": 5}]
    _, confirm, _, _ = _run(svc, full=_full(lines=written))
    kw = confirm.call_args.kwargs
    assert kw["description"] == "two"
    assert kw["confirmed_mixed_codes"] is False


# ---- R2-4b: the probe also finds a stranded / in-flight repoint ------------------------------

def test_enqueued_repoint_is_probed_and_refused_as_already_enqueued():
    out, confirm, uncoded, _ = _run(
        _svc(), state_status="enqueued", full=_full(status="enqueued", source=REPOINT_CONFIRMATION_SOURCE),
        confirm={"status": "already_enqueued"})
    confirm.assert_called_once()
    assert confirm.call_args.kwargs["repoint"] is True
    assert out["enqueued"] == 0 and out["skip_reason"] == "already_enqueued"


def test_confirmed_repoint_recoded_back_to_the_written_item_reports_an_empty_revision():
    # R4b-0 (ii): the recompute SUCCEEDED and found no divergence (the user recoded back): a NEW,
    # EMPTY revision - fresh targets [] travel with the token so confirm can supersede the old approval.
    out, confirm, uncoded, _ = _run(
        _svc(), state_status="confirmed", item_value="item-A",
        full=_full(status="confirmed", source=REPOINT_CONFIRMATION_SOURCE))
    confirm.assert_called_once()
    kw = confirm.call_args.kwargs
    assert kw["repoint"] is True and kw["public_id"] == "cs-1"
    assert kw["repoint_targets"] == [] and kw["repoint_targets_token"] == "6"
    assert out["enqueued"] == 1 and out["repoint"] is True
    uncoded.assert_not_called()


@pytest.mark.parametrize("status,source", [("confirmed", REPOINT_CONFIRMATION_SOURCE),
                                           ("enqueued", REPOINT_CONFIRMATION_SOURCE), ("written", None)])
def test_recompute_that_raises_skips_and_never_calls_confirm(status, source, caplog):
    # M5/I5 (was: bare pending -> confirm recovers the persisted approval): "could not compute" is never
    # "nothing diverges" and never a recovery. Pending AND written candidates alike.
    with patch.object(ExpenseService, "_compute_repoint_divergence", side_effect=RuntimeError("blip")), \
            caplog.at_level(logging.WARNING):
        out, confirm, uncoded, _ = _run(_svc(), state_status=status, full=_full(status=status, source=source))
    confirm.assert_not_called()
    uncoded.assert_not_called()  # never the first-write fallthrough
    assert out["success"] is True and out["enqueued"] == 0 and out["skipped"] is True
    assert out["skip_reason"] == "repoint_recompute_failed" and out["message"] == "Repoint not enqueued"
    assert any("expense.complete.repoint_recompute_failed" in r.getMessage() for r in caplog.records)


def test_probe_returns_the_skip_dict_for_a_raised_recompute():
    svc = _svc()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "confirmed"}]}), \
            patch(FULL_READ, return_value=_full(status="confirmed", source=REPOINT_CONFIRMATION_SOURCE)), \
            patch.object(ExpenseService, "_compute_repoint_divergence", side_effect=RuntimeError("blip")):
        div = _probe(svc)
    assert div == {"public_id": "cs-1", "skip_reason": "repoint_recompute_failed"}


def test_unmapped_local_coding_on_a_written_line_skips_as_repoint_line_unmapped():
    # M5/I5/H: the local sub cost code resolves to NO item -> not "unchanged"; no confirm, no first-write fallthrough
    out, confirm, uncoded, _ = _run(_svc(), resolver=lambda *a, **k: None)
    confirm.assert_not_called()
    uncoded.assert_not_called()
    assert out["skipped"] is True and out["skip_reason"] == "repoint_line_unmapped"


def test_unmapped_line_beside_a_diverged_line_still_skips_the_whole_probe():
    svc = _two_line_svc(_eli("L1", 9, project_id=1, amount=10), _eli("L2", 8, project_id=1, amount=10))
    resolver = lambda sub, **k: SimpleNamespace(value="item-B") if sub == 9 else None
    out, confirm, uncoded, _ = _run(svc, full=_full(lines=TWO_WRITTEN), resolver=resolver)
    confirm.assert_not_called()
    assert out["skip_reason"] == "repoint_line_unmapped"


def test_compute_repoint_divergence_raises_repoint_unresolvable_for_ref_none():
    from entities.expense.business.service import RepointUnresolvable

    svc = _svc()
    local = {"L1": _eli("L1", 9)}
    with pytest.raises(RepointUnresolvable) as ei:
        svc._compute_repoint_divergence("cs-1", _full(), local, "realm", lambda *a, **k: None)
    assert ei.value.reason == "repoint_line_unmapped"


@pytest.mark.parametrize("status", ["confirmed", "enqueued"])
def test_probe_recomputes_targets_and_token_for_a_pending_repoint(status):
    # R4-C: a later completion is a NEW revision - the pending candidate is recomputed, not trusted
    svc = _svc()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": status}]}), \
            patch(FULL_READ, return_value=_full(status=status, source=REPOINT_CONFIRMATION_SOURCE)), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-B")):
        div = _probe(svc)
    assert div["pending"] is True and div["targets_token"] == "6"
    assert div["targets"] == [{"qbo_line_id": "L1", "sub_cost_code_id": 9, "project_id": 1,
                               "description": "d", "repoint_from_item_ref_value": "item-A"}]


def test_probe_reports_an_empty_revision_when_nothing_diverges_any_more():
    svc = _svc()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "confirmed"}]}), \
            patch(FULL_READ, return_value=_full(status="confirmed", source=REPOINT_CONFIRMATION_SOURCE)), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-A")):
        div = _probe(svc)
    assert div["pending"] is True and div["targets"] == [] and div["targets_token"] == "6"


def test_revision_seq_is_present_on_every_returned_revision_shape():
    svc = _svc()
    seq_full = lambda **k: SimpleNamespace(**dict(vars(_full(**k)), confirmation_seq=11))
    # found (written candidate)
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "written"}]}), \
            patch(FULL_READ, return_value=seq_full()), patch(RESOLVER, return_value=SimpleNamespace(value="item-B")):
        found = _probe(svc)
    # pending (diverged), and empty (pulled back)
    pend = seq_full(status="confirmed", source=REPOINT_CONFIRMATION_SOURCE)
    state = {55: [{"public_id": "cs-1", "status": "confirmed"}]}
    with patch(STATE_READ, return_value=state), patch(FULL_READ, return_value=pend), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-B")):
        pending = _probe(svc)
    with patch(STATE_READ, return_value=state), patch(FULL_READ, return_value=pend), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-A")):
        empty = _probe(svc)
    assert found["revision_seq"] == 11 and pending["revision_seq"] == 11 and empty["revision_seq"] == 11
    assert pending["pending"] is True and empty["targets"] == []


def test_probe_reads_line_items_fresh_itself():
    svc = _svc()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "written"}]}), \
            patch(FULL_READ, return_value=_full()), patch(RESOLVER, return_value=SimpleNamespace(value="item-B")):
        div = _probe(svc, SimpleNamespace(realm_id="r"))
    svc._expense_line_item_service.read_by_expense_id.assert_called_once_with(expense_id=55)
    assert div["targets"][0]["sub_cost_code_id"] == 9


def test_completion_no_longer_hands_its_line_items_to_the_probe():
    import inspect

    assert "line_items" not in inspect.signature(ExpenseService._repoint_revision_for_candidate).parameters
    assert "line_items" not in inspect.signature(ExpenseService._enqueue_coding_recode_on_complete).parameters


def test_busy_confirm_is_reported_as_a_skip_with_reason_busy():
    out, confirm, uncoded, _ = _run(_svc(), confirm={"status": "busy"})
    assert out["success"] is True and out["enqueued"] == 0 and out["skipped"] is True
    assert out["skip_reason"] == "busy"
    uncoded.assert_not_called()


def test_pending_candidate_passes_fresh_targets_and_token_to_confirm():
    out, confirm, uncoded, _ = _run(
        _svc(), state_status="confirmed", item_value="item-B",
        full=_full(status="confirmed", source=REPOINT_CONFIRMATION_SOURCE))
    kw = confirm.call_args.kwargs
    assert kw["repoint_targets_token"] == "6" and kw["repoint_targets"][0]["qbo_line_id"] == "L1"
    assert kw["project_id"] == 1 and kw["sub_cost_code_id"] == 9


def test_enqueued_first_write_is_not_a_repoint_candidate():
    out, confirm, uncoded, _ = _run(
        _svc(), state_status="enqueued", full=_full(status="enqueued", source="review_submit"))
    confirm.assert_not_called()
    uncoded.assert_called_once()


def test_already_enqueued_and_repoint_pending_are_nothing_to_do_reasons():
    assert "repoint_pending" in ExpenseService._RECODE_ENQUEUE_NOTHING_TO_DO_REASONS
    assert "already_enqueued" in ExpenseService._RECODE_ENQUEUE_NOTHING_TO_DO_REASONS


# ---- R2-4c: submit loop never re-confirms a pending repoint as a first write -----------------

@pytest.mark.parametrize("status", ["confirmed", "enqueued"])
def test_submit_loop_skips_pending_repoint(status):
    svc = ExpenseService(repo=MagicMock())
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.read_by_expense_id.return_value = []
    state = {"public_id": "cs-1", "status": status, "confirmation_source": REPOINT_CONFIRMATION_SOURCE}
    confirm = MagicMock()
    with patch(STATE_READ, return_value={55: [state]}), patch(CONFIRM, confirm), \
            patch(DOMINANT, return_value={"project_id": 1, "sub_cost_code_id": 9, "description": "d"}):
        out = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)
    confirm.assert_not_called()
    assert out["enqueued"] == 0 and out["items"][0]["reason"] == "repoint_pending"


# ---- R3-E(4) / R3-A(i): mixed codes spans ALL written lines; a record without L1 never targets it ---

def test_mixed_codes_true_when_one_line_diverged_and_a_sibling_carries_another_code():
    svc = _two_line_svc(_eli("L1", 9, project_id=1, amount=10), _eli("L2", 5, project_id=1, amount=10))
    resolver = lambda sub, **k: SimpleNamespace(value="item-B" if sub == 9 else "item-A")
    out, confirm, _, _ = _run(svc, full=_full(lines=TWO_WRITTEN), resolver=resolver)
    kw = confirm.call_args.kwargs
    assert [t["qbo_line_id"] for t in kw["repoint_targets"]] == ["L1"]
    assert kw["confirmed_mixed_codes"] is True


def test_record_without_l1_never_yields_an_l1_target():
    l2_only = [{"qbo_line_id": "L2", "item_ref_value": "item-Y", "sub_cost_code_id": 7}]
    svc = _two_line_svc(_eli("L1", 9, project_id=1, amount=10), _eli("L2", 7, project_id=1, amount=10))
    resolver = lambda sub, **k: SimpleNamespace(value="item-Y" if sub == 7 else "item-H")
    out, confirm, uncoded, _ = _run(svc, full=_full(lines=l2_only), resolver=resolver)
    confirm.assert_not_called()  # no divergence: L1 is not ours


# ---- holding code on a written line is a SKIP (r6b Codex P2): the pull can put a holding code on a
# completed parent's line after the upstream check; a silent skip could empty a pending approval -------

def test_holding_code_on_a_written_line_is_a_skip_that_never_confirms_or_falls_through():
    from shared.lifecycle.completion_gate import HoldingCodeRefused

    def resolver(sub, **kw):
        raise HoldingCodeRefused("h")

    out, confirm, uncoded, _ = _run(_svc(), resolver=resolver)
    assert out["skip_reason"] == "repoint_line_holding"
    confirm.assert_not_called()
    uncoded.assert_not_called()  # never the first-write fallthrough


def test_holding_line_beside_a_diverged_line_still_skips_the_whole_revision():
    # the OTHER line's target must not be written while one written line cannot be judged
    from shared.lifecycle.completion_gate import HoldingCodeRefused

    svc = _two_line_svc(_eli("L1", 9, project_id=1, amount=10), _eli("L2", 8, project_id=2, amount=10))

    def resolver(sub, **kw):
        if sub == 9:
            raise HoldingCodeRefused("h")
        return SimpleNamespace(value="item-B")

    out, confirm, uncoded, _ = _run(svc, full=_full(lines=TWO_WRITTEN), resolver=resolver)
    assert out["skip_reason"] == "repoint_line_holding"
    confirm.assert_not_called()
    uncoded.assert_not_called()


def test_given_expense_is_not_re_read():
    svc = _svc()
    expense = SimpleNamespace(id=55, qbo_id="P", realm_id="realm")
    svc.read_by_id.reset_mock()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "written"}]}), \
            patch(FULL_READ, return_value=_full()), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-B")), \
            patch(CONFIRM, return_value={"status": "enqueued"}) as confirm:
        out = svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True, expense=expense)
    confirm.assert_called_once()
    svc.read_by_id.assert_not_called()
    assert out["enqueued"] == 1


@pytest.mark.parametrize("confirm_status", ["busy", "repoint_revision_lost"])
def test_submit_loop_counts_busy_and_lost_revisions_as_skipped_with_that_reason(confirm_status):
    svc = ExpenseService(repo=MagicMock())
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.read_by_expense_id.return_value = []
    state = {"public_id": "cs-1", "status": "suggested", "confirmation_source": None}
    with patch(STATE_READ, return_value={55: [state]}), patch(CONFIRM, return_value={"status": confirm_status}), \
            patch(DOMINANT, return_value={"project_id": 1, "sub_cost_code_id": 9, "description": "d"}):
        out = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)
    assert out["enqueued"] == 0 and out["skipped"] == 1 and out["invalid"] == 0
    assert out["items"][0]["outcome"] == "skipped" and out["items"][0]["reason"] == confirm_status


# ---- F2: the probe and its confirm run under ONE lock hold; the revision is read BEFORE the lines ------

LOCK_TARGET = "entities.coding_suggestion.business.service.qbo_app_lock"


def _recording_lock(events, *, grant=True):
    from contextlib import contextmanager

    @contextmanager
    def lock(resource, timeout_ms=15000):
        events.append("lock_enter" if grant else "lock_denied")
        try:
            yield grant
        finally:
            events.append("lock_exit")
    return lock


def test_completion_holds_the_lock_across_full_read_then_line_read_then_confirm():
    events = []
    svc = _svc()
    svc._expense_line_item_service.read_by_expense_id.side_effect = (
        lambda **k: events.append("lines_read") or [_eli("L1", 9, project_id=1, amount=10)])
    confirm = MagicMock(side_effect=lambda **k: events.append("confirm") or {"status": "enqueued"})
    state_read = MagicMock(side_effect=lambda ids: events.append("state_read") or {55: [{"public_id": "cs-1", "status": "written"}]})
    with patch(STATE_READ, state_read), \
            patch(FULL_READ, side_effect=lambda pid: events.append("full_read") or _full()), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-B")), patch(CONFIRM, confirm), \
            patch(LOCK_TARGET, _recording_lock(events)):
        out = svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True)
    assert out["enqueued"] == 1
    # the lines are read AFTER the suggestion record (so its seq is bound first), all inside one hold
    assert events == ["state_read", "lock_enter", "full_read", "lines_read", "confirm", "lock_exit"]
    assert confirm.call_args.kwargs["lock_held"] is True  # confirm must not take the lock a second time


def test_a_revision_that_moves_after_the_bound_read_is_never_paired_with_older_lines():
    # Codex's interleaving: a concurrent completion lands right AFTER our suggestion read - seq n -> n+1
    # and the local lines change. We must compute from lines read AFTER the bound read (never an older
    # snapshot labelled with the newer seq), and the approval stays bound to n so confirm refuses it.
    world = {"seq": 3, "sub": 9}

    def read_full(pid):
        snapshot = SimpleNamespace(**dict(vars(_full()), confirmation_seq=world["seq"]))
        world.update(seq=4, sub=8)  # the interleaved writer, immediately after the read
        return snapshot

    svc = _svc()
    svc._expense_line_item_service.read_by_expense_id.side_effect = (
        lambda **k: [_eli("L1", world["sub"], project_id=1, amount=10)])
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "written"}]}), \
            patch(FULL_READ, side_effect=read_full), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-B")):
        div = _probe(svc)
    assert div["revision_seq"] == 3  # the FIRST (bound) read's seq
    assert [t["sub_cost_code_id"] for t in div["targets"]] == [8]  # lines read after the bound revision

    # end to end through the REAL confirm: the item is now at n+1, so the approval is refused unwritten
    from entities.coding_suggestion.business.service import CodingSuggestionService

    world.update(seq=3, sub=9)
    moved = SimpleNamespace(status="written", confirmation_seq=4, written_sync_token="6", written_lines=WRITTEN)
    record = MagicMock()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "written"}]}), \
            patch(FULL_READ, side_effect=read_full), \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-B")), \
            patch("integrations.intuit.qbo.base.client.recode_write_gate_reason", return_value=None), \
            patch.object(CodingSuggestionService, "read_by_public_id", return_value=moved), \
            patch.object(CodingSuggestionService, "record_confirmation", record):
        out = svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True)
    assert out["skip_reason"] == "repoint_revision_lost" and out["enqueued"] == 0
    record.assert_not_called()


def test_revision_seq_is_the_first_reads_seq_even_when_a_second_read_differs():
    # F4(iii): any LATER read of the suggestion (e.g. confirm's own) may see another seq; the probe's
    # label is the first one.
    reads = iter([SimpleNamespace(**dict(vars(_full()), confirmation_seq=11)),
                  SimpleNamespace(**dict(vars(_full()), confirmation_seq=12))])
    svc = _svc()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "written"}]}), \
            patch(FULL_READ, side_effect=lambda pid: next(reads)) as full_read, \
            patch(RESOLVER, return_value=SimpleNamespace(value="item-B")), \
            patch(CONFIRM, return_value={"status": "enqueued"}) as confirm:
        svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True)
    assert confirm.call_args.kwargs["expected_confirmation_seq"] == 11
    assert full_read.call_count == 1  # the probe reads the record exactly once per candidate


def test_lock_denied_at_completion_is_a_busy_skip_with_no_reads_and_no_confirm():
    events = []
    svc = _svc()
    with patch(STATE_READ, return_value={55: [{"public_id": "cs-1", "status": "written"}]}), \
            patch(FULL_READ) as full_read, patch(CONFIRM) as confirm, patch(UNCODED) as uncoded, \
            patch(LOCK_TARGET, _recording_lock(events, grant=False)):
        out = svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True)
    assert out["success"] is True and out["enqueued"] == 0 and out["skipped"] is True
    assert out["skip_reason"] == "busy" and out["message"] == "Repoint not enqueued"
    confirm.assert_not_called()
    full_read.assert_not_called()
    svc._expense_line_item_service.read_by_expense_id.assert_not_called()
    uncoded.assert_not_called()  # never the first-write fallthrough
    assert events == ["lock_denied", "lock_exit"]


def test_the_lock_is_keyed_on_the_candidates_canonical_resource():
    resources = []
    from contextlib import contextmanager

    @contextmanager
    def lock(resource, timeout_ms=15000):
        resources.append((resource, timeout_ms))
        yield True

    svc = _svc()
    with patch(STATE_READ, return_value={55: [{"public_id": "CS-ABC", "status": "written"}]}), \
            patch(FULL_READ, return_value=_full()), patch(RESOLVER, return_value=SimpleNamespace(value="item-B")), \
            patch(CONFIRM, return_value={"status": "enqueued"}), patch(LOCK_TARGET, lock):
        svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True)
    assert resources == [("qbo_coding_suggestion:cs-abc", 10_000)]


# ---- F5: a missing recompute input is a SKIP, never an empty revision ------------------------------

MISSING_LOCAL = [{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5},
                 {"qbo_line_id": "L9", "item_ref_value": "item-A", "sub_cost_code_id": 5}]  # L9: no local line


@pytest.mark.parametrize("status,source", [("written", None), ("confirmed", REPOINT_CONFIRMATION_SOURCE),
                                           ("enqueued", REPOINT_CONFIRMATION_SOURCE)])
@pytest.mark.parametrize("case,reason", [("missing_local", "repoint_line_missing_local"),
                                         ("uncoded", "repoint_line_uncoded")])
def test_missing_recompute_inputs_skip_with_that_reason_and_never_confirm(status, source, case, reason):
    if case == "missing_local":
        svc, lines = _svc(), MISSING_LOCAL
    else:
        svc, lines = _two_line_svc(_eli("L1", 9), _eli("L2", None)), TWO_WRITTEN
    out, confirm, uncoded, _ = _run(svc, state_status=status, item_value="item-A",
                                    full=_full(status=status, lines=lines, source=source))
    confirm.assert_not_called()  # the persisted approval is left exactly as it was
    uncoded.assert_not_called()  # and never the first-write fallthrough
    assert out["success"] is True and out["enqueued"] == 0 and out["skipped"] is True
    assert out["skip_reason"] == reason and out["message"] == "Repoint not enqueued"


def test_compute_raises_repoint_unresolvable_with_the_missing_input_reason():
    from entities.expense.business.service import RepointUnresolvable

    resolver = lambda *a, **k: SimpleNamespace(value="item-A")
    with pytest.raises(RepointUnresolvable) as ei:
        _svc()._compute_repoint_divergence("cs-1", _full(lines=MISSING_LOCAL), {"L1": _eli("L1", 9)}, "realm", resolver)
    assert ei.value.reason == "repoint_line_missing_local"
    with pytest.raises(RepointUnresolvable) as ei:
        _svc()._compute_repoint_divergence("cs-1", _full(), {"L1": _eli("L1", None)}, "realm", resolver)
    assert ei.value.reason == "repoint_line_uncoded"


def test_empty_revision_still_reported_when_every_written_line_has_a_coded_local_counterpart_equal_to_written():
    svc = _two_line_svc(_eli("L1", 5), _eli("L2", 5))
    out, confirm, uncoded, _ = _run(svc, state_status="confirmed", item_value="item-A",
                                    full=_full(status="confirmed", lines=TWO_WRITTEN,
                                               source=REPOINT_CONFIRMATION_SOURCE))
    kw = confirm.call_args.kwargs
    assert kw["repoint_targets"] == [] and kw["repoint_targets_token"] == "6"
    assert out["enqueued"] == 1 and out["repoint"] is True
