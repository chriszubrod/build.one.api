"""U-551b-f1 bite 2: CodingSuggestionService.confirm(repoint=True)."""
import uuid
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
from entities.coding_suggestion.business.service import CodingSuggestionService
from shared.lifecycle.completion_gate import HoldingCodeRefused

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
ELI_SVC = "entities.expense_line_item.business.service.ExpenseLineItemService"
OUTBOX = "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
PURCHASE_REPO = "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository"
WRITTEN = [{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5}]


def _item(status="written", token="6", lines=WRITTEN):
    return SimpleNamespace(
        public_id=PUBLIC_ID, status=status, qbo_purchase_qbo_id="p-1", qbo_purchase_id=9,
        realm_id="realm", expense_id=55, written_sync_token=token, written_lines=lines,
        confirmation_seq=3,
    )


def _svc(item):
    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=item)
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=4))
    svc.mark_enqueued = MagicMock()
    return svc


def _pending(status):
    item = _item(status=status)
    item.confirmation_source = REPOINT_CONFIRMATION_SOURCE
    item.confirmation_seq = 5
    item.repoint_targets = TARGETS
    return item


TARGETS = [{"qbo_line_id": "L1", "sub_cost_code_id": 2, "project_id": 1, "description": "d",
            "repoint_from_item_ref_value": "item-A"}]


_AUTO = object()


def _confirm(svc, *, repoint=True, elis=(), gate=None, resolver=SimpleNamespace(value="i"), targets=TARGETS,
             token="6", seq=_AUTO):
    # U-551b-f1 M2: a repoint caller (the completion probe) hands the revision it READ; default = the item's.
    if seq is _AUTO:
        seq = svc.read_by_public_id.return_value.confirmation_seq if repoint else None
    eli = MagicMock()
    eli.read_by_expense_id.return_value = list(elis)
    outbox = MagicMock()
    repo = MagicMock()
    with patch(GATE, return_value=gate), patch(RESOLVER, return_value=resolver), \
            patch(ELI_SVC, return_value=eli), patch(OUTBOX, return_value=outbox), \
            patch(PURCHASE_REPO, return_value=repo):
        res = svc.confirm(public_id=PUBLIC_ID, project_id=1, sub_cost_code_id=2, description="d",
                          was_overridden=True, user_id=17, allow_multi_line=True, repoint=repoint,
                          repoint_targets=targets if repoint else None,
                          repoint_targets_token=token if repoint else None,
                          expected_confirmation_seq=seq)
    return res, outbox, repo


def test_repoint_uses_written_token_and_source():
    svc = _svc(_item())
    res, outbox, repo = _confirm(svc)
    assert res == {"status": "enqueued", "enqueued": True}
    kw = svc.record_confirmation.call_args.kwargs
    assert kw["confirmation_source"] == REPOINT_CONFIRMATION_SOURCE
    assert kw["expected_sync_token"] == "6"
    assert kw["status"] == "confirmed"
    repo.read_by_id.assert_not_called()
    outbox.enqueue.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=4)  # R4-D


@pytest.mark.parametrize("item,expected", [
    (_item(status="enqueued"), "not_written"),
    (_item(token=None), "no_written_record"),
    (_item(lines=[]), "no_written_record"),
])
def test_repoint_refusals_write_nothing(item, expected):
    svc = _svc(item)
    res, outbox, _ = _confirm(svc)
    assert res["status"] == expected
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_repoint_gate_off_writes_nothing():
    svc = _svc(_item())
    res, outbox, _ = _confirm(svc, gate="qbo_writes_disabled")
    assert res["status"] == "writes_disabled"
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_repoint_holding_code_on_other_line_raises():
    other = SimpleNamespace(id=2, qbo_id="OTHER", sub_cost_code_id=999)
    svc = _svc(_item())
    with patch("entities.coding_suggestion.business.service.assert_no_holding_on_lines",
               side_effect=HoldingCodeRefused("holding")):
        with pytest.raises(HoldingCodeRefused):
            _confirm(svc, elis=[other])
    svc.record_confirmation.assert_not_called()


def test_non_repoint_still_snapshots_staging_token():
    svc = _svc(_item(status="suggested", token=None, lines=[]))
    svc._count_uncategorized_purchase_lines = MagicMock(return_value=1)
    svc._uncategorized_staging_qbo_line_ids = MagicMock(return_value=[])
    res, _, repo = _confirm(svc, repoint=False)
    repo.read_by_id.assert_called_once()
    kw = svc.record_confirmation.call_args.kwargs
    assert kw["confirmation_source"] == "review_submit"


# ---- R2-4a: stranded / in-flight repoint ----------------------------------------------------

def test_stranded_confirmed_repoint_recovers_without_record_confirmation():
    item = _pending("confirmed")
    svc = _svc(item)
    res, outbox, _ = _confirm(svc)  # R4-C: fresh targets EQUAL the persisted ones -> recovery as before
    assert res == {"status": "enqueued", "enqueued": True, "recovered": True}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=5)


def test_stranded_confirmed_repoint_recovers_even_with_no_coding_args():
    item = _pending("confirmed")
    svc = _svc(item)
    with patch(GATE, return_value=None), patch(OUTBOX, return_value=MagicMock()) as ob:
        res = svc.confirm(public_id=PUBLIC_ID, project_id=None, sub_cost_code_id=None, description=None,
                          was_overridden=True, user_id=17, allow_multi_line=True, repoint=True,
                          expected_confirmation_seq=5)
    assert res["recovered"] is True
    svc.record_confirmation.assert_not_called()


def test_repoint_without_targets_refuses_with_zero_writes():
    for targets in (None, []):
        svc = _svc(_item())
        res, outbox, _ = _confirm(svc, targets=targets)
        assert res == {"status": "no_repoint_targets"}
        svc.record_confirmation.assert_not_called()
        outbox.enqueue.assert_not_called()


def test_repoint_stores_targets_json_and_first_write_clears_them():
    svc = _svc(_item())
    _confirm(svc)
    assert json.loads(svc.record_confirmation.call_args.kwargs["repoint_targets_json"]) == TARGETS
    svc2 = _svc(_item(status="suggested", token=None, lines=[]))
    svc2._count_uncategorized_purchase_lines = MagicMock(return_value=1)
    svc2._uncategorized_staging_qbo_line_ids = MagicMock(return_value=[])
    _confirm(svc2, repoint=False)
    assert svc2.record_confirmation.call_args.kwargs.get("repoint_targets_json") is None


def test_enqueued_repoint_is_already_enqueued_and_writes_nothing():
    item = _pending("enqueued")
    svc = _svc(item)
    res, outbox, _ = _confirm(svc)
    assert res == {"status": "already_enqueued"}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_confirmed_with_non_repoint_source_is_not_written():
    item = _item(status="confirmed")
    item.confirmation_source = "review_submit"
    svc = _svc(item)
    res, outbox, _ = _confirm(svc)
    assert res["status"] == "not_written"
    svc.record_confirmation.assert_not_called()


# ---- R2-8e: WHICH lines reach the holding-code assertion ------------------------------------

def test_repoint_holding_assertion_sees_only_the_other_lines():
    written_line = SimpleNamespace(id=1, qbo_id="L1", sub_cost_code_id=5)
    other = SimpleNamespace(id=2, qbo_id="OTHER", sub_cost_code_id=999)
    svc = _svc(_item())
    with patch("entities.coding_suggestion.business.service.assert_no_holding_on_lines") as guard:
        res, _, _ = _confirm(svc, elis=[written_line, other])
    assert res["status"] == "enqueued"
    guard.assert_called_once()
    assert [li.id for li in guard.call_args.args[0]] == [2]


# ---- R2-6: written_lines validates the WHOLE record -----------------------------------------

def _model(json_text):
    from entities.coding_suggestion.business.model import CodingSuggestion

    fields = {f: None for f in CodingSuggestion.__dataclass_fields__}
    fields["written_lines_json"] = json_text
    return CodingSuggestion(**fields)


def test_written_lines_round_trips_a_valid_record():
    good = [{"qbo_line_id": "L1", "item_ref_value": "i", "sub_cost_code_id": 5},
            {"qbo_line_id": "L2", "item_ref_value": "j", "sub_cost_code_id": None}]
    assert _model(json.dumps(good)).written_lines == good


@pytest.mark.parametrize("bad", [
    "[{}]",
    '[{"qbo_line_id": "", "item_ref_value": "i"}]',
    '[{"qbo_line_id": "L1"}]',
    '[{"qbo_line_id": 1, "item_ref_value": "i"}]',
    '[{"qbo_line_id": "L1", "item_ref_value": "i", "sub_cost_code_id": "5"}]',
    '[{"qbo_line_id": "L1", "item_ref_value": "i"}, 3]',
    '[{"qbo_line_id": "L1", "item_ref_value": "i"}, {}]',
    "{}", "null", "not json",
    pytest.param("[" * 100000 + "]" * 100000, id="deep-nesting"),
])
def test_written_lines_fails_closed_on_any_bad_entry(bad):
    assert _model(bad).written_lines == []


# ---- R2-8c: _from_db maps the written record ------------------------------------------------

def test_from_db_maps_written_columns():
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    row = SimpleNamespace(Id=1, PublicId=PUBLIC_ID, WrittenSyncToken="6", WrittenLinesJson='[{"a": 1}]')
    out = CodingSuggestionRepository()._from_db(row)
    assert out is not None
    assert out.written_sync_token == "6" and out.written_lines_json == '[{"a": 1}]'


# ---- R3-C: repoint_targets validation, mapping, repo param ----------------------------------

GOOD_T = {"qbo_line_id": "L1", "sub_cost_code_id": 9, "project_id": None, "description": None,
          "repoint_from_item_ref_value": "item-A"}


def _tmodel(json_text):
    m = _model(None)
    m.repoint_targets_json = json_text
    return m


def test_repoint_targets_round_trips_valid_record():
    other = dict(GOOD_T, qbo_line_id="L2", project_id=3, description="x")
    assert _tmodel(json.dumps([GOOD_T, other])).repoint_targets == [GOOD_T, other]


@pytest.mark.parametrize("bad", [
    json.dumps([dict(GOOD_T, qbo_line_id="")]),
    json.dumps([dict(GOOD_T, sub_cost_code_id="9")]),
    json.dumps([dict(GOOD_T, sub_cost_code_id=True)]),
    json.dumps([dict(GOOD_T, project_id=True)]),
    json.dumps([dict(GOOD_T, project_id="1")]),
    json.dumps([dict(GOOD_T, description=5)]),
    json.dumps([dict(GOOD_T, repoint_from_item_ref_value="")]),
    json.dumps([GOOD_T, 3]),
    json.dumps([GOOD_T, {}]),
    "{}", "not json", None,
    pytest.param("[" * 100000 + "]" * 100000, id="deep-nesting"),
])
def test_repoint_targets_fails_closed_on_any_bad_entry(bad):
    assert _tmodel(bad).repoint_targets is None  # R4b-0: None = absent/malformed, never confused with []


def test_from_db_maps_repoint_targets_json():
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    row = SimpleNamespace(Id=1, PublicId=PUBLIC_ID, RepointTargetsJson="[]")
    assert CodingSuggestionRepository()._from_db(row).repoint_targets_json == "[]"


def test_repo_record_confirmation_passes_repoint_targets_param():
    from entities.coding_suggestion.persistence import repo as repo_mod

    with patch.object(repo_mod, "get_connection"), patch.object(repo_mod, "call_procedure") as cp, \
            patch.object(repo_mod.CodingSuggestionRepository, "_from_db", return_value=None):
        repo_mod.CodingSuggestionRepository().record_confirmation(
            public_id=PUBLIC_ID, repoint_targets_json="[1]")
    assert cp.call_args.kwargs["params"]["RepointTargetsJson"] == "[1]"


# ---- R3-E(1): read_state_by_expense_ids maps ConfirmationSource through the real mapper ------

def test_read_state_by_expense_ids_maps_confirmation_source():
    from entities.coding_suggestion.persistence import repo as repo_mod

    row = SimpleNamespace(ExpenseId=55, CodingItemPublicId="p", Status="confirmed", SuggestedProjectId=1,
                          SuggestedSubCostCodeId=2, ConfirmedProjectId=1, ConfirmedSubCostCodeId=2,
                          FlagReason=None, ConfirmationSource=REPOINT_CONFIRMATION_SOURCE)
    conn = MagicMock()
    conn.__enter__.return_value.cursor.return_value.fetchall.return_value = [row]
    with patch.object(repo_mod, "get_connection", return_value=conn), patch.object(repo_mod, "call_procedure"):
        out = repo_mod.CodingSuggestionRepository().read_state_by_expense_ids([55])
    assert out[55][0]["confirmation_source"] == REPOINT_CONFIRMATION_SOURCE


# ---- R4-B: stale targets are refused at confirm -----------------------------------------------

def test_token_only_stale_is_refused_with_zero_writes():
    # R4b-2(3): membership + prior item are FINE (L1:item-A is in the record); only the token moved on
    item = _item(token="8")
    svc = _svc(item)
    res, outbox, _ = _confirm(svc, token="6")
    assert res == {"status": "repoint_targets_stale"}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()
    svc.mark_enqueued.assert_not_called()


def test_membership_only_stale_is_refused_with_zero_writes():
    # R4b-2(3): the token MATCHES; the written record no longer carries the targeted line
    item = _item(token="6", lines=[{"qbo_line_id": "L2", "item_ref_value": "item-Y", "sub_cost_code_id": 6}])
    svc = _svc(item)
    res, outbox, _ = _confirm(svc, token="6")
    assert res == {"status": "repoint_targets_stale"}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()
    svc.mark_enqueued.assert_not_called()


@pytest.mark.parametrize("token,targets", [
    (None, TARGETS),
    ("5", TARGETS),
    ("6", [dict(TARGETS[0], qbo_line_id="L9")]),                         # not in written_lines
    ("6", [dict(TARGETS[0], repoint_from_item_ref_value="item-Z")]),     # prior item differs
    ("6", TARGETS + [dict(TARGETS[0], qbo_line_id="L9")]),
])
def test_stale_token_membership_and_prior_item_are_refused(token, targets):
    svc = _svc(_item())
    res, outbox, _ = _confirm(svc, token=token, targets=targets)
    assert res == {"status": "repoint_targets_stale"}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_fresh_matching_set_passes_confirm():
    svc = _svc(_item())
    res, outbox, _ = _confirm(svc, token="6")
    assert res["status"] == "enqueued"
    svc.record_confirmation.assert_called_once()


# ---- R4-C: a later completion is a NEW revision -----------------------------------------------

NEW_TARGETS = [dict(TARGETS[0], sub_cost_code_id=7, description="new")]


def test_confirmed_equal_targets_recovers_without_record_confirmation():
    svc = _svc(_pending("confirmed"))
    res, outbox, _ = _confirm(svc, targets=[dict(t) for t in reversed(TARGETS)])
    assert res == {"status": "enqueued", "enqueued": True, "recovered": True}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_called_once()


def test_enqueued_equal_targets_is_already_enqueued():
    svc = _svc(_pending("enqueued"))
    res, outbox, _ = _confirm(svc)
    assert res == {"status": "already_enqueued"}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()


@pytest.mark.parametrize("status", ["confirmed", "enqueued"])
def test_different_targets_record_a_new_revision(status, caplog):
    import logging

    svc = _svc(_pending(status))
    with caplog.at_level(logging.WARNING):
        res, outbox, _ = _confirm(svc, targets=NEW_TARGETS)
    assert res == {"status": "enqueued", "enqueued": True}
    kw = svc.record_confirmation.call_args.kwargs
    assert json.loads(kw["repoint_targets_json"]) == NEW_TARGETS
    assert kw["confirmation_source"] == REPOINT_CONFIRMATION_SOURCE and kw["expected_sync_token"] == "6"
    outbox.enqueue.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=4)
    assert any("coding_suggestion.repoint.targets_replaced" in r.getMessage() and PUBLIC_ID in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("status", ["confirmed", "enqueued"])
def test_different_stale_targets_are_refused_before_replacing(status):
    svc = _svc(_pending(status))
    res, outbox, _ = _confirm(svc, targets=NEW_TARGETS, token="3")
    assert res == {"status": "repoint_targets_stale"}
    svc.record_confirmation.assert_not_called()


# ---- R4-D: recovery's mark_enqueued cannot regress a terminal state ---------------------------

def test_recovery_with_terminal_row_reports_already_terminal():
    item = _pending("confirmed")
    svc = _svc(item)
    svc.mark_enqueued.return_value = None  # the seq-guarded sproc matched nothing
    res, outbox, _ = _confirm(svc)
    assert res == {"status": "already_terminal"}
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=5)


def test_service_mark_enqueued_forwards_the_seq():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.mark_enqueued(PUBLIC_ID, expected_confirmation_seq=9)
    svc.repo.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=9)


def test_repo_mark_enqueued_passes_the_seq_param():
    from entities.coding_suggestion.persistence import repo as repo_mod

    with patch.object(repo_mod, "get_connection"), patch.object(repo_mod, "call_procedure") as cp, \
            patch.object(repo_mod.CodingSuggestionRepository, "_from_db", return_value=None):
        repo_mod.CodingSuggestionRepository().mark_enqueued(PUBLIC_ID, expected_confirmation_seq=7)
        repo_mod.CodingSuggestionRepository().mark_enqueued(PUBLIC_ID)
    assert cp.call_args_list[0].kwargs["params"] == {"PublicId": PUBLIC_ID, "ExpectedConfirmationSeq": 7}
    assert cp.call_args_list[1].kwargs["params"]["ExpectedConfirmationSeq"] is None


# ---- R4-E(1): the service wrapper forwards written_lines_json --------------------------------

def test_service_mark_written_forwards_written_lines_json():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.mark_written(PUBLIC_ID, sync_token="9", expected_confirmation_seq=3, written_lines_json="[1]")
    svc.repo.mark_written.assert_called_once_with(
        PUBLIC_ID, sync_token="9", expected_confirmation_seq=3, written_lines_json="[1]")


# ---- R4b-0: None (absent) vs [] (an explicit empty revision) --------------------------------

def test_model_distinguishes_absent_from_an_explicit_empty_list():
    assert _tmodel(None).repoint_targets is None
    assert _tmodel("").repoint_targets is None
    assert _tmodel("not json").repoint_targets is None
    assert _tmodel("{}").repoint_targets is None
    assert _tmodel("[]").repoint_targets == []


@pytest.mark.parametrize("status", ["confirmed", "enqueued"])
def test_empty_fresh_targets_supersede_nonempty_persisted_as_a_new_revision(status, caplog):
    import logging

    svc = _svc(_pending(status))
    with caplog.at_level(logging.WARNING):
        res, outbox, _ = _confirm(svc, targets=[])
    assert res == {"status": "enqueued", "enqueued": True}
    assert svc.record_confirmation.call_args.kwargs["repoint_targets_json"] == "[]"
    outbox.enqueue.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=4)
    assert any("coding_suggestion.repoint.targets_replaced" in r.getMessage() for r in caplog.records)


def test_empty_fresh_targets_equal_to_empty_persisted_is_plain_recovery():
    item = _pending("confirmed")
    item.repoint_targets = []
    svc = _svc(item)
    res, outbox, _ = _confirm(svc, targets=[])
    assert res == {"status": "enqueued", "enqueued": True, "recovered": True}
    svc.record_confirmation.assert_not_called()


def test_empty_fresh_targets_with_a_moved_token_are_stale():
    svc = _svc(_pending("confirmed"))
    res, outbox, _ = _confirm(svc, targets=[], token="3")
    assert res == {"status": "repoint_targets_stale"}
    svc.record_confirmation.assert_not_called()


def test_absent_fresh_targets_recover_the_persisted_work_as_is():
    svc = _svc(_pending("confirmed"))
    res, outbox, _ = _confirm(svc, targets=None)
    assert res == {"status": "enqueued", "enqueued": True, "recovered": True}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_called_once()


def test_written_item_with_empty_fresh_targets_does_nothing():
    svc = _svc(_item())
    res, outbox, _ = _confirm(svc, targets=[])
    assert res == {"status": "no_repoint_targets"}
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()
    svc.mark_enqueued.assert_not_called()


# ---- R4b-1: the confirmation write is guarded by the revision it read ------------------------

def _store_record(state):
    """A fake record_confirmation applying the SQL predicate against `state` ({"seq","status"})."""
    def record(**kwargs):
        expected = kwargs.get("expected_confirmation_seq")
        if expected is not None and (state["seq"] != expected
                                     or state["status"] not in ("written", "confirmed", "enqueued")):
            return None
        state["seq"] += 1
        return SimpleNamespace(confirmation_seq=state["seq"])
    return record


def test_repoint_passes_the_callers_seq_and_first_write_does_not_guard():
    item = _item()
    item.confirmation_seq = 5
    svc = _svc(item)
    _confirm(svc)
    assert svc.record_confirmation.call_args.kwargs["expected_confirmation_seq"] == 5
    svc2 = _svc(_item(status="suggested", token=None, lines=[]))
    svc2._count_uncategorized_purchase_lines = MagicMock(return_value=1)
    svc2._uncategorized_staging_qbo_line_ids = MagicMock(return_value=[])
    _confirm(svc2, repoint=False)
    assert svc2.record_confirmation.call_args.kwargs.get("expected_confirmation_seq") is None


def test_older_completion_cannot_overwrite_a_newer_approval_against_the_same_token():
    # A read the record at seq 5 and probed targets B (paused); C then confirmed targets C (seq 5 -> 6);
    # A resumes, passes the token check, but its guarded write matches nothing.
    item = _item()
    item.confirmation_seq = 5
    svc = _svc(item)
    state = {"seq": 6, "status": "confirmed"}
    svc.record_confirmation = MagicMock(side_effect=_store_record(state))
    res, outbox, _ = _confirm(svc)
    assert res == {"status": "repoint_revision_lost"}
    assert state["seq"] == 6
    outbox.enqueue.assert_not_called()
    svc.mark_enqueued.assert_not_called()


@pytest.mark.parametrize("terminal", ["changed_in_qbo", "error"])
def test_replacement_after_a_terminal_mark_is_refused(terminal, caplog):
    import logging

    item = _pending("enqueued")  # the replacement read `enqueued` seq 5 ...
    svc = _svc(item)
    state = {"seq": 5, "status": terminal}  # ... the worker then marked seq 5 terminal
    svc.record_confirmation = MagicMock(side_effect=_store_record(state))
    with caplog.at_level(logging.WARNING):
        res, outbox, _ = _confirm(svc, targets=NEW_TARGETS)
    assert res == {"status": "repoint_revision_lost"}
    assert svc.record_confirmation.call_args.kwargs["expected_confirmation_seq"] == 5
    outbox.enqueue.assert_not_called()
    svc.mark_enqueued.assert_not_called()
    assert any("repoint_revision_lost" in r.getMessage() for r in caplog.records)


def test_unrecorded_first_write_stops_before_the_enqueue():
    svc = _svc(_item(status="suggested", token=None, lines=[]))
    svc._count_uncategorized_purchase_lines = MagicMock(return_value=1)
    svc._uncategorized_staging_qbo_line_ids = MagicMock(return_value=[])
    svc.record_confirmation.return_value = None
    res, outbox, _ = _confirm(svc, repoint=False)
    assert res == {"status": "not_recorded"}
    outbox.enqueue.assert_not_called()
    svc.mark_enqueued.assert_not_called()


def test_service_and_repo_forward_expected_confirmation_seq():
    from entities.coding_suggestion.persistence import repo as repo_mod

    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.record_confirmation(public_id=PUBLIC_ID, expected_confirmation_seq=5)
    assert svc.repo.record_confirmation.call_args.kwargs["expected_confirmation_seq"] == 5
    with patch.object(repo_mod, "get_connection"), patch.object(repo_mod, "call_procedure") as cp, \
            patch.object(repo_mod.CodingSuggestionRepository, "_from_db", return_value=None):
        repo_mod.CodingSuggestionRepository().record_confirmation(public_id=PUBLIC_ID, expected_confirmation_seq=5)
        repo_mod.CodingSuggestionRepository().record_confirmation(public_id=PUBLIC_ID)
    assert cp.call_args_list[0].kwargs["params"]["ExpectedConfirmationSeq"] == 5
    assert cp.call_args_list[1].kwargs["params"]["ExpectedConfirmationSeq"] is None


# ---- R4b-2: the comparison is a SET over (line, sub cost code, project, description, prior item) ----

def test_reversed_two_entry_targets_compare_equal():
    t1 = TARGETS[0]
    t2 = dict(t1, qbo_line_id="L2")
    item = _pending("confirmed")
    item.written_lines = WRITTEN + [{"qbo_line_id": "L2", "item_ref_value": "item-A", "sub_cost_code_id": 5}]
    item.repoint_targets = [t1, t2]
    svc = _svc(item)
    res, outbox, _ = _confirm(svc, targets=[t2, t1])
    assert res == {"status": "enqueued", "enqueued": True, "recovered": True}
    svc.record_confirmation.assert_not_called()


@pytest.mark.parametrize("field,value", [("sub_cost_code_id", 7), ("project_id", 2), ("description", "other")])
def test_a_target_differing_in_exactly_one_field_is_a_different_revision(field, value):
    svc = _svc(_pending("confirmed"))
    res, outbox, _ = _confirm(svc, targets=[dict(TARGETS[0], **{field: value})])
    assert res == {"status": "enqueued", "enqueued": True}
    svc.record_confirmation.assert_called_once()


# ---- U-551b-f1 round 6 (M1/M2): the revision is the CALLER's, and confirm runs under the suggestion lock ----

LOCK_TARGET = "entities.coding_suggestion.business.service.qbo_app_lock"


class _Float(float):
    """A seq that EQUALS the caller's int but is not one - a fallback to the item's own read is visible."""


def _confirm_kwargs(**over):
    kw = dict(public_id=PUBLIC_ID, project_id=1, sub_cost_code_id=2, description="d", was_overridden=True,
              user_id=17, allow_multi_line=True, repoint=True, repoint_targets=TARGETS, repoint_targets_token="6")
    kw.update(over)
    return kw


def _run_confirm(svc, **over):
    outbox = MagicMock()
    with patch(GATE, return_value=None), patch(RESOLVER, return_value=SimpleNamespace(value="i")), \
            patch(ELI_SVC, return_value=MagicMock(read_by_expense_id=MagicMock(return_value=[]))), \
            patch(OUTBOX, return_value=outbox), patch(PURCHASE_REPO, return_value=MagicMock()):
        return svc.confirm(**_confirm_kwargs(**over)), outbox


def test_older_completion_handed_seq_n_while_the_item_is_at_n_plus_1_is_revision_lost_with_zero_writes():
    item = _item()
    item.confirmation_seq = 6  # a newer completion already recorded revision n+1
    svc = _svc(item)
    res, outbox = _run_confirm(svc, expected_confirmation_seq=5)
    assert res == {"status": "repoint_revision_lost"}
    svc.record_confirmation.assert_not_called()
    svc.mark_enqueued.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_repoint_without_expected_confirmation_seq_is_invalid_and_touches_nothing():
    svc = _svc(_item())
    res, outbox = _run_confirm(svc, expected_confirmation_seq=None)
    assert res == {"status": "invalid", "reason": "revision required"}
    svc.read_by_public_id.assert_not_called()
    svc.record_confirmation.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_non_repoint_confirm_needs_no_seq_and_records_unguarded():
    svc = _svc(_item(status="suggested", token=None, lines=[]))
    svc._count_uncategorized_purchase_lines = MagicMock(return_value=1)
    svc._uncategorized_staging_qbo_line_ids = MagicMock(return_value=[])
    res, _ = _run_confirm(svc, repoint=False, repoint_targets=None, repoint_targets_token=None)
    assert res["status"] == "enqueued"
    assert svc.record_confirmation.call_args.kwargs["expected_confirmation_seq"] is None


def test_recovery_mark_enqueued_receives_the_callers_seq_not_the_one_read():
    item = _pending("confirmed")
    item.confirmation_seq = _Float(5)  # == 5 so the revision check passes, but is NOT the caller's object
    svc = _svc(item)
    res, _ = _run_confirm(svc, expected_confirmation_seq=5)
    assert res["recovered"] is True
    sent = svc.mark_enqueued.call_args.kwargs["expected_confirmation_seq"]
    assert sent == 5 and type(sent) is int


@pytest.mark.parametrize("status", ["confirmed", "enqueued"])
def test_replacing_record_confirmation_receives_the_callers_seq_not_the_one_read(status):
    item = _pending(status)
    item.confirmation_seq = _Float(5)
    svc = _svc(item)
    res, _ = _run_confirm(svc, expected_confirmation_seq=5, repoint_targets=NEW_TARGETS)
    assert res == {"status": "enqueued", "enqueued": True}
    sent = svc.record_confirmation.call_args.kwargs["expected_confirmation_seq"]
    assert sent == 5 and type(sent) is int


def test_plain_repoint_record_confirmation_receives_the_callers_seq_not_the_one_read():
    item = _item()
    item.confirmation_seq = _Float(3)
    svc = _svc(item)
    _run_confirm(svc, expected_confirmation_seq=3)
    sent = svc.record_confirmation.call_args.kwargs["expected_confirmation_seq"]
    assert sent == 3 and type(sent) is int


def test_empty_revision_confirm_with_no_project_or_sub_cost_code_is_enqueued():
    # r4b P3 (i): the real probe contract for an EMPTY revision passes NO coding args
    item = _pending("confirmed")
    svc = _svc(item)
    res, outbox = _run_confirm(svc, project_id=None, sub_cost_code_id=None, description=None,
                               repoint_targets=[], expected_confirmation_seq=5)
    assert res == {"status": "enqueued", "enqueued": True}
    assert json.loads(svc.record_confirmation.call_args.kwargs["repoint_targets_json"]) == []
    outbox.enqueue.assert_called_once()


def _recording_lock(events, *, grant=True, resources=None, timeouts=None):
    from contextlib import contextmanager

    @contextmanager
    def lock(resource, timeout_ms=15000):
        if resources is not None:
            resources.append(resource)
        if timeouts is not None:
            timeouts.append(timeout_ms)
        events.append("lock_enter" if grant else "lock_denied")
        try:
            yield grant
        finally:
            events.append("lock_exit")
    return lock


def test_lock_denied_returns_busy_with_zero_writes_on_the_suggestion_resource():
    events, resources, timeouts = [], [], []
    svc = _svc(_item())
    with patch(LOCK_TARGET, _recording_lock(events, grant=False, resources=resources, timeouts=timeouts)):
        res, outbox = _run_confirm(svc, expected_confirmation_seq=3)
    assert res == {"status": "busy"}
    assert resources == [f"qbo_coding_suggestion:{PUBLIC_ID}"]
    assert timeouts == [10_000]
    svc.read_by_public_id.assert_not_called()
    svc.record_confirmation.assert_not_called()
    svc.mark_enqueued.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_busy_is_logged(caplog):
    import logging

    with patch(LOCK_TARGET, _recording_lock([], grant=False)), caplog.at_level(logging.WARNING):
        _run_confirm(_svc(_item()), expected_confirmation_seq=3)
    assert any("coding_suggestion.lock_busy" in r.getMessage() for r in caplog.records)


def test_confirm_takes_the_lock_after_the_gate_and_before_the_read_and_releases_after_mark_enqueued():
    events = []
    svc = _svc(_item())
    svc.read_by_public_id = MagicMock(side_effect=lambda *a, **k: events.append("read") or _item())
    svc.mark_enqueued = MagicMock(side_effect=lambda *a, **k: events.append("mark_enqueued"))
    svc.record_confirmation = MagicMock(
        side_effect=lambda *a, **k: events.append("record_confirmation") or SimpleNamespace(confirmation_seq=4))
    outbox = MagicMock()
    outbox.enqueue.side_effect = lambda **k: events.append("enqueue")
    with patch(GATE, side_effect=lambda: events.append("gate")), \
            patch(LOCK_TARGET, _recording_lock(events)), patch(RESOLVER, return_value=SimpleNamespace(value="i")), \
            patch(ELI_SVC, return_value=MagicMock(read_by_expense_id=MagicMock(return_value=[]))), \
            patch(OUTBOX, return_value=outbox), patch(PURCHASE_REPO, return_value=MagicMock()):
        res = svc.confirm(**_confirm_kwargs(expected_confirmation_seq=3))
    assert res["status"] == "enqueued"
    assert events == ["gate", "lock_enter", "read", "record_confirmation", "enqueue", "mark_enqueued", "lock_exit"]


def test_gate_off_never_reaches_the_lock():
    events = []
    svc = _svc(_item())
    with patch(GATE, return_value="qbo_writes_disabled"), patch(LOCK_TARGET, _recording_lock(events)):
        res = svc.confirm(**_confirm_kwargs(expected_confirmation_seq=3))
    assert res["status"] == "writes_disabled" and events == []


def test_the_lock_is_released_when_confirm_raises():
    events = []
    svc = _svc(_item())
    svc.read_by_public_id = MagicMock(side_effect=RuntimeError("db"))
    with patch(GATE, return_value=None), patch(LOCK_TARGET, _recording_lock(events)):
        with pytest.raises(RuntimeError):
            svc.confirm(**_confirm_kwargs(expected_confirmation_seq=3))
    assert events == ["lock_enter", "lock_exit"]


def test_the_lock_wrapper_resolves_qbo_app_lock_at_call_time_and_keys_one_resource():
    from entities.coding_suggestion.business.service import (
        coding_suggestion_lock,
        coding_suggestion_lock_resource,
    )

    assert coding_suggestion_lock_resource("abc") == "qbo_coding_suggestion:abc"
    seen = []
    with patch(LOCK_TARGET, _recording_lock([], resources=seen, timeouts=seen)):
        with coding_suggestion_lock("abc", timeout_ms=1234) as got:
            assert got is True
    assert seen == ["qbo_coding_suggestion:abc", 1234]


# ---- F1: the lock key is canonical - every spelling of one suggestion id contends on ONE resource ----

UPPER = "ABCDEFAB-1234-5678-9ABC-DEF012345678"
LOWER = "abcdefab-1234-5678-9abc-def012345678"


def test_lock_resource_canonicalizes_every_spelling_of_a_uuid():
    from entities.coding_suggestion.business.service import coding_suggestion_lock_resource as res

    assert res(UPPER) == res(LOWER) == res("{" + UPPER + "}") == res("  " + UPPER + " ") == f"qbo_coding_suggestion:{LOWER}"
    assert res(UPPER.replace("-", "")) == res(LOWER)  # the un-hyphenated spelling parses too
    # r6b twin P3: braces AND padding together must still parse (strip precedes the uuid parse)
    assert res(" {" + UPPER + "} ") == res(LOWER)
    assert res(uuid.UUID(LOWER)) == res(LOWER)  # a uuid.UUID object, not only strings
    # r6b Codex P1: SQL Server converts a string by its first 36 chars (38 with braces) and ignores a
    # suffix, so "<guid>wrong" READS the same row as "<guid>" - the lock must agree
    assert res(UPPER + "wrong") == res(LOWER)
    assert res("{" + UPPER + "}junk") == res(LOWER)
    assert res("urn:uuid:" + LOWER) == res(LOWER)


def test_lock_resource_falls_back_to_strip_lower_for_a_non_uuid():
    from entities.coding_suggestion.business.service import coding_suggestion_lock_resource as res

    assert res("  Not-A-UUID ") == "qbo_coding_suggestion:not-a-uuid"
    assert res("abc") == "qbo_coding_suggestion:abc"


def test_a_drain_holding_the_upper_spelling_makes_confirm_with_the_lower_spelling_busy():
    from contextlib import contextmanager

    from entities.coding_suggestion.business.service import coding_suggestion_lock

    held = set()
    attempts = []

    @contextmanager
    def exclusive_lock(resource, timeout_ms=15000):
        attempts.append(resource)
        if resource in held:
            yield False
            return
        held.add(resource)
        try:
            yield True
        finally:
            held.discard(resource)

    svc = _svc(_item())
    with patch(LOCK_TARGET, exclusive_lock):
        with coding_suggestion_lock(UPPER, timeout_ms=5_000) as drain_has_it:  # the drain's spelling
            assert drain_has_it is True
            with patch(GATE, return_value=None):
                res = svc.confirm(**_confirm_kwargs(public_id=LOWER, expected_confirmation_seq=3))
    assert res == {"status": "busy"}
    assert attempts == [f"qbo_coding_suggestion:{LOWER}"] * 2  # both spellings asked for the SAME resource
    svc.read_by_public_id.assert_not_called()
    svc.record_confirmation.assert_not_called()


# ---- F2: confirm(lock_held=True) - the caller holds the lock; confirm never takes it ------------------

def test_lock_held_confirm_never_takes_the_lock_and_still_confirms():
    events = []
    svc = _svc(_item())
    with patch(LOCK_TARGET, _recording_lock(events)):
        res, outbox = _run_confirm(svc, expected_confirmation_seq=3, lock_held=True)
    assert res["status"] == "enqueued"
    assert events == []  # neither the wrapper nor qbo_app_lock was reached
    svc.record_confirmation.assert_called_once()
    outbox.enqueue.assert_called_once()


def test_lock_held_confirm_does_not_acquire_even_when_the_lock_would_be_denied():
    svc = _svc(_item())
    with patch(LOCK_TARGET, _recording_lock([], grant=False)):
        res, _ = _run_confirm(svc, expected_confirmation_seq=3, lock_held=True)
    assert res["status"] == "enqueued"  # a denied acquire would have been {"status": "busy"}


def test_lock_held_confirm_still_runs_the_gate_check():
    svc = _svc(_item())
    with patch(GATE, return_value="qbo_writes_disabled"), patch(LOCK_TARGET, _recording_lock([])):
        res = svc.confirm(**_confirm_kwargs(expected_confirmation_seq=3), lock_held=True)
    assert res == {"status": "writes_disabled", "reason": "qbo_writes_disabled"}
    svc.read_by_public_id.assert_not_called()


def test_lock_held_confirm_still_requires_the_revision_for_a_repoint():
    svc = _svc(_item())
    res, outbox = _run_confirm(svc, expected_confirmation_seq=None, lock_held=True)
    assert res == {"status": "invalid", "reason": "revision required"}
    svc.read_by_public_id.assert_not_called()
    outbox.enqueue.assert_not_called()


def test_lock_held_defaults_to_false_and_acquires():
    import inspect

    assert inspect.signature(CodingSuggestionService.confirm).parameters["lock_held"].default is False
    events = []
    with patch(LOCK_TARGET, _recording_lock(events)):
        _run_confirm(_svc(_item()), expected_confirmation_seq=3)
    assert events[0] == "lock_enter" and events[-1] == "lock_exit"


# ---- F6(iii): qbo_app_lock must be EXCLUSIVE and TRANSACTION-owned (the granted-lock fixture cannot see it)

def test_qbo_app_lock_acquires_exclusive_transaction_owned():
    # A Shared mode would let confirm and the drain hold the lock together, and a Session owner would
    # outlive the pooled connection's rollback-on-close; the patched-lock fixtures cannot see either.
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.joinpath("integrations/intuit/qbo/base/locking.py").read_text()
    assert re.search(r"@LockMode\s*=\s*'Exclusive'", src)
    assert re.search(r"@LockOwner\s*=\s*'Transaction'", src)
    assert "'Shared'" not in src and "'Session'" not in src


# ---- the cockpit router: busy -> 409, releasing a claim this request took ----------------------

def _route_busy(*, claimed_by):
    from fastapi import HTTPException

    from entities.coding_suggestion.api.router import confirm_expense_coding_item_router
    from entities.coding_suggestion.api.schemas import ConfirmCodingSuggestionRequest

    service = MagicMock()
    service.read_by_public_id.return_value = SimpleNamespace(claimed_by_user_id=claimed_by)
    service.is_multi_line_purchase.return_value = False
    service.claim.return_value = SimpleNamespace(claimed_by_user_id=17)
    service.confirm.return_value = {"status": "busy"}
    body = ConfirmCodingSuggestionRequest(
        project_public_id="00000000-0000-0000-0000-000000000010",
        sub_cost_code_public_id="00000000-0000-0000-0000-000000000020",
        description="x", was_overridden=False,
    )
    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), \
            patch("entities.coding_suggestion.api.router.current_user_id") as uid, \
            patch("entities.coding_suggestion.api.router._assert_can_access_coding_suggestion"), \
            patch("entities.coding_suggestion.api.router.assert_can_access_project"), \
            patch("entities.project.business.service.ProjectService") as ps, \
            patch("entities.sub_cost_code.business.service.SubCostCodeService") as scs:
        uid.get.return_value = 17
        ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        scs.return_value.read_by_public_id.return_value = SimpleNamespace(id=20)
        with pytest.raises(HTTPException) as exc:
            confirm_expense_coding_item_router(PUBLIC_ID, body, _={"can_update": True})
    return exc.value, service


def test_cockpit_router_maps_busy_to_409_and_releases_a_claim_taken_by_the_request():
    err, service = _route_busy(claimed_by=None)
    assert err.status_code == 409 and err.detail == "coding_suggestion_busy"
    service.claim.assert_called_once()
    service.release.assert_called_once_with(public_id=PUBLIC_ID, user_id=17)


def test_cockpit_router_busy_does_not_release_a_claim_it_did_not_take():
    err, service = _route_busy(claimed_by=17)
    assert err.status_code == 409 and err.detail == "coding_suggestion_busy"
    service.claim.assert_not_called()
    service.release.assert_not_called()


from tests._coding_access_open import coding_access_open  # noqa: F401  (U-652d4 autouse fixture)
