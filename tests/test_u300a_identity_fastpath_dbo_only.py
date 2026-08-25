"""
U-300a — the dbo-only identity fast path (base/identity_fastpath.py::run_identity_fastpath_dbo_only).

Wave-5 "trust dbo alone" pilot (memory `project_qbo_trust_dbo_identity_alone`): for a family that
has RETIRED its `qbo.*` mapping table, `run_identity_fastpath`'s CONSISTENT/MISSING/CONFLICT
machinery has nothing left to cross-check against — the filtered unique index on
`(QboId, RealmId)` plus `Set<Entity>QboIdentity`'s theft-clear UPDATE already guarantee a single
holder. This sibling function trusts a direct hit outright and, on a miss, serializes the
caller's candidate-resolution + identity-stamp behind a dedicated app lock (keyed on the entity's
own `(qbo_id, realm_id)`, NOT the `qbo_mapping_create:*` namespace `create_race_lock` uses) so two
concurrent misses for the same external identity can't both mint a competing row and silently
orphan one via theft-clear.

These tests pin the helper's own contract in isolation — no DB/QBO I/O; `qbo_app_lock` is always
patched, per the house convention `test_u304_rollback_lock.py` established for this module's other
lock-based helper.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from integrations.intuit.qbo.base.identity_fastpath import (
    FastPathOutcome,
    run_identity_fastpath_dbo_only,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted as _granted_lock
from test_u304_rollback_lock import _recording_lock_factory

LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"


@contextmanager
def _denied_lock(*_args, **_kwargs):
    yield False


def _harness(*, direct=None, **overrides):
    """Build a run_identity_fastpath_dbo_only kwargs dict plus the Mocks it drives."""
    spy = SimpleNamespace(
        read_direct=Mock(return_value=direct),
        apply=Mock(side_effect=lambda e: e),
        resolve_candidate=Mock(return_value=SimpleNamespace(id=77)),
        stamp_identity=Mock(side_effect=lambda candidate: candidate),
    )
    kwargs = dict(
        qbo_id="Q-99",
        realm_id="realm-1",
        entity_label="Attachment",
        external_label="QboAttachable",
        lock_resource_label="Attachment",
        read_direct_by_qbo_identity=spy.read_direct,
        resolve_candidate=spy.resolve_candidate,
        stamp_identity=spy.stamp_identity,
        apply_fields=spy.apply,
    )
    kwargs.update(overrides)
    return kwargs, spy


# --- falsy qbo_id short-circuit ---------------------------------------------


@patch(LOCK_PATCH_TARGET)
def test_falsy_qbo_id_short_circuits_without_reading_or_locking(mock_lock):
    kwargs, spy = _harness()
    kwargs["qbo_id"] = None
    outcome = run_identity_fastpath_dbo_only(**kwargs)
    assert outcome == FastPathOutcome(hit=False, entity=None)
    spy.read_direct.assert_not_called()
    mock_lock.assert_not_called()


# --- direct hit: no lock needed at all --------------------------------------


@patch(LOCK_PATCH_TARGET)
def test_direct_hit_applies_fields_and_never_touches_the_lock(mock_lock):
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct)
    outcome = run_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.apply.assert_called_once_with(direct)
    spy.resolve_candidate.assert_not_called()
    spy.stamp_identity.assert_not_called()
    mock_lock.assert_not_called()


def test_direct_hit_with_apply_fields_omitted_resolves_without_writing():
    """Mirrors run_identity_fastpath's attachable shape: identity resolution only,
    field work happens downstream in the caller."""
    direct = SimpleNamespace(id=55)
    kwargs, spy = _harness(direct=direct)
    kwargs.pop("apply_fields")
    outcome = run_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.resolve_candidate.assert_not_called()


def test_direct_hit_apply_returning_none_notifies_then_raises():
    """U-316: the callback still fires (kept per Decision 2), but the raise is
    now unconditional -- a caller can no longer under-implement on_apply_
    returned_none (log-but-forget-to-raise) and get a silent entity=None back."""
    direct = SimpleNamespace(id=55)
    kwargs, _ = _harness(direct=direct)
    on_none = Mock()
    kwargs["apply_fields"] = Mock(return_value=None)
    kwargs["on_apply_returned_none"] = on_none
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_identity_fastpath_dbo_only(**kwargs)
    on_none.assert_called_once_with(direct)


def test_direct_hit_apply_returning_none_without_a_callback_now_raises():
    """U-316 mutation-proven case: apply_fields -> None with NO
    on_apply_returned_none wired used to return FastPathOutcome(hit=True,
    entity=None) silently -- it now raises unconditionally, so a caller
    can no longer omit protection by omitting the callback param."""
    kwargs, _ = _harness(direct=SimpleNamespace(id=55))
    kwargs["apply_fields"] = Mock(return_value=None)
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_identity_fastpath_dbo_only(**kwargs)


# --- miss: fail-closed on lock timeout --------------------------------------


@patch(LOCK_PATCH_TARGET, _denied_lock)
def test_miss_fails_closed_on_lock_timeout_without_creating():
    kwargs, spy = _harness(direct=None)
    with pytest.raises(RuntimeError, match="Could not acquire dbo-only identity create lock"):
        run_identity_fastpath_dbo_only(**kwargs)
    spy.resolve_candidate.assert_not_called()
    spy.stamp_identity.assert_not_called()


# --- miss: genuine miss under the lock creates via the caller's callbacks ---


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_genuine_miss_under_lock_resolves_and_stamps():
    kwargs, spy = _harness(direct=None)
    stamped = SimpleNamespace(id=77, qbo_id="Q-99")
    spy.stamp_identity.side_effect = None
    spy.stamp_identity.return_value = stamped
    outcome = run_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True and outcome.entity is stamped
    spy.resolve_candidate.assert_called_once_with()
    spy.stamp_identity.assert_called_once_with(spy.resolve_candidate.return_value)
    spy.apply.assert_not_called()  # apply_fields is for an EXISTING hit, not a fresh mint
    # The re-read under the lock is the whole point of this path — it must
    # actually run again with identical args, not be assumed from the outer miss.
    assert spy.read_direct.call_args_list == [call("Q-99", "realm-1")] * 2


# --- miss: a racer wins the lock first --------------------------------------


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_racer_discovered_under_lock_is_adopted_not_duplicated():
    """The scenario this whole function exists to close: two concurrent misses
    for the same identity. The second one MUST adopt the racer's row instead of
    minting a competing one via resolve_candidate/stamp_identity."""
    racer_row = SimpleNamespace(id=90)
    kwargs, spy = _harness(direct=None)
    spy.read_direct.side_effect = [None, racer_row]  # outer miss, then a racer under the lock
    outcome = run_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True and outcome.entity is racer_row
    spy.resolve_candidate.assert_not_called()
    spy.stamp_identity.assert_not_called()
    spy.apply.assert_called_once_with(racer_row)


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_racer_discovered_under_lock_with_apply_returning_none():
    """U-316: same unconditional-raise contract applies to the race-resolved
    hit branch, not just the outer direct-hit branch -- both flow through
    the same `_apply()` closure."""
    racer_row = SimpleNamespace(id=90)
    kwargs, spy = _harness(direct=None)
    spy.read_direct.side_effect = [None, racer_row]
    on_none = Mock()
    kwargs["apply_fields"] = Mock(return_value=None)
    kwargs["on_apply_returned_none"] = on_none
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_identity_fastpath_dbo_only(**kwargs)
    on_none.assert_called_once_with(racer_row)
    spy.resolve_candidate.assert_not_called()


# --- miss: stamp_identity returning None (U-316) ----------------------------


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_genuine_miss_stamp_identity_returning_none_raises():
    """U-316 mutation-proven case: before this fix, `stamped = stamp_identity(
    candidate); return FastPathOutcome(hit=True, entity=stamped)` never
    checked `stamped` at all -- a concurrent-delete between resolve_candidate
    and the stamp lock would silently propagate entity=None to the caller.
    Now it raises, symmetric to the apply-path guard above."""
    kwargs, spy = _harness(direct=None)
    spy.stamp_identity.side_effect = None
    spy.stamp_identity.return_value = None
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_identity_fastpath_dbo_only(**kwargs)
    spy.resolve_candidate.assert_called_once_with()
    spy.stamp_identity.assert_called_once_with(spy.resolve_candidate.return_value)


# --- lock resource key: disjoint from create_race_lock's namespace ---------


@patch(LOCK_PATCH_TARGET)
def test_lock_resource_key_shape_is_disjoint_from_mapping_create_namespace(mock_lock):
    """Must never collide with `qbo_mapping_create:*` (create_race_lock's own
    prefix, U-304) — the two lock different critical sections and must never be
    combined in the same call stack (deadlock note in both docstrings)."""
    recorded, _recording_lock = _recording_lock_factory()
    mock_lock.side_effect = _recording_lock
    kwargs, _ = _harness(direct=None)
    run_identity_fastpath_dbo_only(**kwargs)

    assert recorded == ["qbo_dbo_identity_create:Attachment:Q-99:realm-1"]
    assert not recorded[0].startswith("qbo_mapping_create:")


@patch(LOCK_PATCH_TARGET)
def test_lock_resource_key_handles_missing_realm_id(mock_lock):
    recorded, _recording_lock = _recording_lock_factory()
    mock_lock.side_effect = _recording_lock
    kwargs, _ = _harness(direct=None)
    kwargs["realm_id"] = None
    run_identity_fastpath_dbo_only(**kwargs)

    assert recorded == ["qbo_dbo_identity_create:Attachment:Q-99:"]


# --- the lock is held across the WHOLE critical section ---------------------
#
# Mutation target: if the re-read, resolve_candidate, or stamp_identity were
# ever moved outside the `with qbo_app_lock(...)` block, this asserted order
# would break.


@patch(LOCK_PATCH_TARGET)
def test_lock_is_held_across_reread_resolve_and_stamp(mock_lock):
    call_order = []

    @contextmanager
    def _tracking_lock(*_args, **_kwargs):
        call_order.append("lock_acquired")
        try:
            yield True
        finally:
            call_order.append("lock_released")

    mock_lock.side_effect = _tracking_lock

    def _read_direct(_qbo_id, _realm_id):
        call_order.append("read_direct")
        return None

    def _resolve_candidate():
        call_order.append("resolve_candidate")
        return SimpleNamespace(id=77)

    def _stamp_identity(candidate):
        call_order.append("stamp_identity")
        return candidate

    run_identity_fastpath_dbo_only(
        qbo_id="Q-99",
        realm_id="realm-1",
        entity_label="Attachment",
        external_label="QboAttachable",
        lock_resource_label="Attachment",
        read_direct_by_qbo_identity=_read_direct,
        resolve_candidate=_resolve_candidate,
        stamp_identity=_stamp_identity,
    )

    assert call_order == [
        "read_direct",  # the outer, unlocked check
        "lock_acquired",
        "read_direct",  # the re-read, INSIDE the lock
        "resolve_candidate",
        "stamp_identity",
        "lock_released",
    ]
