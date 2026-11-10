"""U-551b-f1-f10: a confirm never strands a claim on a 500 and never records a revision it cannot enqueue.

Every test names the mutant it kills. Nothing here opens a DB/HTTP connection: the outbox repository is a
MagicMock and the suggestion lock is granted autouse by conftest."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
from entities.coding_suggestion.business.service import CodingSuggestionService
from integrations.intuit.qbo.outbox.business.service import (
    QboOutboxDeadLetterExistsError,
    QboOutboxService,
)
from tests.test_u551bf1f2_confirm_status_guard import (
    GATE, OUTBOX, PUBLIC_ID, RESOLVER, _confirm, _item, _rerecord_patches, _route, _route_service, _svc,
)


KEY = dict(entity_type="CodingSuggestion", entity_public_id=PUBLIC_ID, kind="recode_purchase_line")


def _repo(pending=None, dead_letter=None):
    """A stub outbox repository: ``pending`` / ``dead_letter`` are what the two probe reads return."""
    repo = MagicMock()
    repo.read_pending_by_entity.return_value = pending
    repo.read_dead_letter_by_entity.return_value = dead_letter
    return repo


def _dead_letter_repo():
    return _repo(dead_letter=SimpleNamespace(public_id="dl-1"))


def _outbox_with(repo):
    """A patch target whose QboOutboxService() is the REAL service over a stub repo."""
    return patch(OUTBOX, side_effect=lambda: QboOutboxService(repo=repo))


# ---- router: the generic 500 path -------------------------------------------------------------------

def test_router_releases_a_claim_taken_here_when_confirm_raises_a_db_error():
    # kills: the generic `except Exception` branch omitting _release_if_claimed (claim stranded 900 s)
    service = _route_service(None, raises=RuntimeError("db down"))
    with pytest.raises(RuntimeError, match="db down"):  # unclassified: re-raised unchanged -> app 500 handler
        _route(service)
    service.claim.assert_called_once()
    service.release.assert_called_once_with(public_id=PUBLIC_ID, user_id=17)


def test_router_keeps_a_claim_the_caller_already_held_when_confirm_raises_a_db_error():
    # kills: releasing unconditionally on the 500 path (drops a claim this request did not take)
    service = _route_service(None, claimed_by=17, raises=RuntimeError("db down"))
    with pytest.raises(RuntimeError, match="db down"):  # unclassified: re-raised unchanged -> app 500 handler
        _route(service)
    service.claim.assert_not_called()
    service.release.assert_not_called()


# ---- service: refuse BEFORE recording ---------------------------------------------------------------

def _assert_refused_unrecorded(svc, repo):
    # kills: a wrong-key pre-check (kind/entity_type/entity_public_id) reading a different target
    repo.read_pending_by_entity.assert_called_once_with(**KEY)
    repo.read_dead_letter_by_entity.assert_called_once_with(**KEY)
    svc.record_confirmation.assert_not_called()
    svc.mark_enqueued.assert_not_called()
    repo.create.assert_not_called()


@patch(GATE, return_value=None)
def test_fresh_pending_suggestion_with_a_dead_letter_is_refused_without_recording(_gate):
    # kills: pre-check removed/moved after record_confirmation -> a wasted revision n+1 is recorded
    svc = _svc(_item("pending", seq=0))
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=1))
    repo = _dead_letter_repo()
    r, c, e, purchases = _rerecord_patches()
    with _outbox_with(repo), r, c, e, purchases as pr:
        pr.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        with pytest.raises(QboOutboxDeadLetterExistsError):
            _confirm(svc)
    _assert_refused_unrecorded(svc, repo)


@patch(GATE, return_value=None)
def test_confirmed_retry_with_different_coding_and_a_dead_letter_is_refused_without_recording(_gate):
    # kills: pre-check skipped on the guard-returned-None (different coding) path -> revision n+2 wasted
    svc = _svc(_item("confirmed", seq=7))
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=8))
    repo = _dead_letter_repo()
    r, c, e, purchases = _rerecord_patches()
    with _outbox_with(repo), r, c, e, purchases as pr:
        pr.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        with pytest.raises(QboOutboxDeadLetterExistsError):
            _confirm(svc, project_id=303, sub_cost_code_id=404)
    _assert_refused_unrecorded(svc, repo)


@patch(GATE, return_value=None)
def test_equal_coding_recovery_with_a_dead_letter_is_refused_before_enqueue_and_mark(_gate):
    # kills: enqueue raises before mark; no record on the recovery path (mark_enqueued / record_confirmation
    # reached despite the dead letter)
    svc = _svc(_item("confirmed", seq=7))
    repo = _dead_letter_repo()
    with _outbox_with(repo), patch(RESOLVER, side_effect=AssertionError("recovery never resolves")):
        with pytest.raises(QboOutboxDeadLetterExistsError):
            _confirm(svc)
    _assert_refused_unrecorded(svc, repo)
    # kills: dead-letter read BEFORE the pending read (wrong order); each read exactly once (no enqueue re-read)
    assert [c[0] for c in repo.method_calls if c[0].startswith('read_')] == [
        'read_pending_by_entity', 'read_dead_letter_by_entity']


@patch(GATE, return_value=None)
def test_repoint_with_a_dead_letter_is_refused_without_recording(_gate):
    # kills: pre-check missing on the repoint path -> a repoint revision recorded with no live outbox row
    item = _item("written", record=True, seq=3)
    svc = _svc(item)
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=4))
    repo = _dead_letter_repo()
    targets = [{"qbo_line_id": "L1", "item_ref_value": "ITEM-B", "sub_cost_code_id": 6}]
    r, c, e, purchases = _rerecord_patches()
    with _outbox_with(repo), r, c, e, purchases, \
            patch.object(CodingSuggestionService, "_repoint_targets_stale", return_value=False):
        with pytest.raises(QboOutboxDeadLetterExistsError):
            svc.confirm(public_id=PUBLIC_ID, project_id=1, sub_cost_code_id=1, description=None,
                        was_overridden=True, user_id=1, repoint=True, repoint_targets=targets,
                        expected_confirmation_seq=3)
    _assert_refused_unrecorded(svc, repo)


@patch(GATE, return_value=None)
def test_repoint_recovery_with_a_dead_letter_is_refused_before_mark(_gate):
    # kills: enqueue raises before mark; no record on the repoint recovery path
    item = _item("confirmed", REPOINT_CONFIRMATION_SOURCE, seq=5, record=True)
    svc = _svc(item)
    repo = _dead_letter_repo()
    with _outbox_with(repo):
        with pytest.raises(QboOutboxDeadLetterExistsError):
            svc.confirm(public_id=PUBLIC_ID, project_id=1, sub_cost_code_id=1, description=None,
                        was_overridden=True, user_id=1, repoint=True, repoint_targets=None,
                        expected_confirmation_seq=5)
    _assert_refused_unrecorded(svc, repo)


# ---- one source of the message ----------------------------------------------------------------------

def test_precheck_and_enqueue_raise_the_same_class_carrying_the_dead_letter_id():
    # kills: enqueue() keeping a divergent inline copy of the refusal (class/message/dead-letter id drift)
    repo = _dead_letter_repo()
    svc = QboOutboxService(repo=repo)
    with pytest.raises(QboOutboxDeadLetterExistsError) as pre:
        svc.find_coalesce_target_or_raise_dead_letter(**KEY)
    with pytest.raises(QboOutboxDeadLetterExistsError) as enq:
        svc.enqueue(realm_id="realm-1", **KEY)
    assert str(pre.value) == str(enq.value) and "dl-1" in str(pre.value)
    repo.create.assert_not_called()


def test_precheck_is_silent_and_writes_nothing_when_no_dead_letter():
    # kills: a pre-check that raises unconditionally or writes
    repo = _repo()
    QboOutboxService(repo=repo).find_coalesce_target_or_raise_dead_letter(**KEY)
    repo.create.assert_not_called()
    repo.update_ready_after.assert_not_called()


# ---- no dead letter: order unchanged ----------------------------------------------------------------

@patch(GATE, return_value=None)
def test_without_a_dead_letter_record_then_enqueue_then_mark_in_order(_gate):
    # kills: pre-check reordering/replacing the write sequence (record -> enqueue -> mark_enqueued)
    svc = _svc(_item("pending", seq=0))
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=5))
    order = MagicMock()
    order.attach_mock(svc.record_confirmation, "record")
    order.attach_mock(svc.mark_enqueued, "mark")
    r, c, e, purchases = _rerecord_patches()
    with patch(OUTBOX) as outbox, r, c, e, purchases as pr:
        order.attach_mock(outbox.return_value.enqueue, "enqueue")
        pr.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        result = _confirm(svc)
    assert result == {"status": "enqueued", "enqueued": True}
    assert [c[0] for c in order.mock_calls] == ["record", "enqueue", "mark"]
    outbox.return_value.find_coalesce_target_or_raise_dead_letter.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=5)


# ---- coexistence: pending row B + dead_letter row A -> coalesce, never refuse ---------------------------

def _coexist_repo():
    return _repo(pending=SimpleNamespace(id=7, row_version=b"v", public_id="pending-B"),
                 dead_letter=SimpleNamespace(public_id="dl-A"))


@patch(GATE, return_value=None)
def test_record_path_with_pending_and_dead_letter_coalesces_instead_of_refusing(_gate):
    # kills: pre-check refusing on a dead_letter row alone (stale approval drains from B)
    svc = _svc(_item("pending", seq=0))
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=1))
    repo = _coexist_repo()
    r, c, e, purchases = _rerecord_patches()
    with _outbox_with(repo), r, c, e, purchases as pr:
        pr.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        result = _confirm(svc)
    assert result == {"status": "enqueued", "enqueued": True}
    svc.record_confirmation.assert_called_once()
    repo.update_ready_after.assert_called_once()
    assert repo.update_ready_after.call_args.kwargs["id"] == 7
    repo.create.assert_not_called()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=1)


@patch(GATE, return_value=None)
def test_recovery_path_with_pending_and_dead_letter_coalesces_and_marks(_gate):
    # kills: recovery tail refusing on dead_letter alone / skipping mark_enqueued
    svc = _svc(_item("confirmed", seq=7))
    repo = _coexist_repo()
    with _outbox_with(repo), patch(RESOLVER, side_effect=AssertionError("recovery never resolves")):
        result = _confirm(svc)
    assert result["status"] == "enqueued" and result["enqueued"] is True
    repo.update_ready_after.assert_called_once()
    repo.create.assert_not_called()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=7)


def test_enqueue_coexistence_coalesces_and_dead_letter_only_raises_same_message():
    # kills: enqueue() consulting dead letters before/despite a coalesce target; message drift on the refusal
    repo = _coexist_repo()
    out = QboOutboxService(repo=repo).enqueue(realm_id="realm-1", **KEY)
    repo.update_ready_after.assert_called_once()
    repo.create.assert_not_called()
    assert out is not None
    only = _dead_letter_repo()
    with pytest.raises(QboOutboxDeadLetterExistsError) as err:
        QboOutboxService(repo=only).enqueue(realm_id="realm-1", **KEY)
    assert err.value.dead_letter_public_id == "dl-1" and "dl-1" in str(err.value)
    only.create.assert_not_called()


@patch(GATE, return_value=None)
def test_happy_path_each_probe_reads_pending_and_dead_letter_exactly_once(_gate):
    # kills: duplicated reads inside one probe (enqueue keeping its inline pending/dead-letter read next to
    # the shared one): pre-check + enqueue = exactly 2 of each, never 3-4
    svc = _svc(_item("pending", seq=0))
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=1))
    repo = _repo()
    repo.create.return_value = SimpleNamespace(public_id="new")
    r, c, e, purchases = _rerecord_patches()
    with _outbox_with(repo), r, c, e, purchases as pr:
        pr.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        _confirm(svc)
    assert repo.read_pending_by_entity.call_count == 2
    assert repo.read_dead_letter_by_entity.call_count == 2
    repo.read_pending_by_entity.assert_called_with(**KEY)
    repo.read_dead_letter_by_entity.assert_called_with(**KEY)
    repo.create.assert_called_once()
    # enqueue() alone: one read of each
    solo = _repo()
    QboOutboxService(repo=solo).enqueue(realm_id="realm-1", **KEY)
    assert solo.read_pending_by_entity.call_count == 1 and solo.read_dead_letter_by_entity.call_count == 1


from tests._coding_access_open import coding_access_open  # noqa: F401  (U-652d4 autouse fixture)
