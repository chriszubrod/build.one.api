"""
U-361 — the dbo-only LINE identity fast path
(base/identity_fastpath.py::run_line_identity_fastpath_dbo_only).

The line-item analog of `run_identity_fastpath_dbo_only` (U-300a) and the mapping-free
analog of `run_line_identity_fastpath` (U-293): for a line family whose
`qbo.<Parent>Line<Entity>LineItem` mapping table is retired (U-349 program, Wave C —
vendorcredit_line_item first), the direct read keys on `(parent_local_id, qbo_line_id)`
against the family's own `UQ_<Entity>LineItem_<Parent>Id_QboId` index, a direct hit is
trusted outright, and a MISS creates+stamps INSIDE a parent+line-scoped create lock
(design §3, Option A). New at this layer: the U-354/U-355 identity-stamp rollback is a
helper-level guarantee — a stamp that raises or does not land rolls the fresh row back
and re-raises, so a clone (U-362/363/364) cannot strand an unstamped orphan line.

These tests pin the helper's own contract in isolation — no DB/QBO I/O; `qbo_app_lock`
is always patched, per the house convention test_u304_rollback_lock.py established for
this module's other lock-based helpers. Mirrors test_u300a_identity_fastpath_dbo_only.py's
harness shape.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from integrations.intuit.qbo.base.identity_fastpath import (
    FastPathOutcome,
    run_line_identity_fastpath_dbo_only,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_denied as _denied_lock
from conftest import mock_qbo_app_lock_granted as _granted_lock
from test_u304_rollback_lock import _recording_lock_factory

LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"


def _harness(*, direct=None, **overrides):
    """Build a run_line_identity_fastpath_dbo_only kwargs dict plus the Mocks it drives.
    The default stamp returns a re-read carrying the resolved identity (the
    contract), so the helper's "stamp did not land" guard stays quiet unless a
    test overrides it on purpose."""
    spy = SimpleNamespace(
        read_direct=Mock(return_value=direct),
        apply=Mock(side_effect=lambda e: e),
        resolve_candidate=Mock(return_value=SimpleNamespace(id=77, public_id="pub-77")),
        stamp_identity=Mock(
            side_effect=lambda candidate: SimpleNamespace(
                id=candidate.id, public_id=candidate.public_id, qbo_id="3", realm_id="realm-1",
            )
        ),
        rollback_candidate=Mock(),
    )
    kwargs = dict(
        parent_local_id=19146,
        qbo_line_id="3",
        entity_label="BillCreditLineItem",
        external_label="QboVendorCreditLine",
        lock_resource_label="BillCreditLineItem",
        read_direct_by_parent_and_qbo_line_id=spy.read_direct,
        resolve_candidate=spy.resolve_candidate,
        stamp_identity=spy.stamp_identity,
        rollback_candidate=spy.rollback_candidate,
        apply_fields=spy.apply,
    )
    kwargs.update(overrides)
    return kwargs, spy


# --- falsy qbo_line_id short-circuit -----------------------------------------


@patch(LOCK_PATCH_TARGET)
def test_falsy_qbo_line_id_short_circuits_without_reading_or_locking(mock_lock):
    kwargs, spy = _harness()
    kwargs["qbo_line_id"] = None
    outcome = run_line_identity_fastpath_dbo_only(**kwargs)
    assert outcome == FastPathOutcome(hit=False, entity=None)
    spy.read_direct.assert_not_called()
    spy.resolve_candidate.assert_not_called()
    mock_lock.assert_not_called()


# --- direct hit: trusted outright, no lock, no create, no stamp ----------------


@patch(LOCK_PATCH_TARGET)
def test_direct_hit_applies_fields_and_never_touches_the_lock(mock_lock):
    direct = SimpleNamespace(id=55, qbo_id="3")
    kwargs, spy = _harness(direct=direct)
    outcome = run_line_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.apply.assert_called_once_with(direct)
    spy.resolve_candidate.assert_not_called()
    spy.stamp_identity.assert_not_called()
    spy.rollback_candidate.assert_not_called()
    mock_lock.assert_not_called()


def test_direct_read_is_parent_scoped_not_a_bare_line_id():
    """Mutation target for the dbo-native resolution: the read MUST carry the
    parent — QBO reuses line ids across every parent transaction."""
    direct = SimpleNamespace(id=55, qbo_id="3")
    kwargs, spy = _harness(direct=direct)
    run_line_identity_fastpath_dbo_only(**kwargs)
    spy.read_direct.assert_called_once_with(19146, "3")


def test_direct_hit_with_apply_fields_omitted_resolves_without_writing():
    direct = SimpleNamespace(id=55, qbo_id="3")
    kwargs, spy = _harness(direct=direct)
    kwargs.pop("apply_fields")
    outcome = run_line_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True and outcome.entity is direct
    spy.resolve_candidate.assert_not_called()


def test_direct_hit_apply_returning_none_notifies_then_raises():
    direct = SimpleNamespace(id=55, qbo_id="3")
    kwargs, _ = _harness(direct=direct)
    on_none = Mock()
    kwargs["apply_fields"] = Mock(return_value=None)
    kwargs["on_apply_returned_none"] = on_none
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    on_none.assert_called_once_with(direct)


def test_direct_hit_apply_returning_none_without_a_callback_still_raises():
    """U-316's contract carried over: a caller cannot omit protection by
    omitting the callback — hit=True never carries entity=None."""
    kwargs, _ = _harness(direct=SimpleNamespace(id=55, qbo_id="3"))
    kwargs["apply_fields"] = Mock(return_value=None)
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_line_identity_fastpath_dbo_only(**kwargs)


# --- miss: fail-closed on lock timeout ----------------------------------------


@patch(LOCK_PATCH_TARGET, _denied_lock)
def test_miss_fails_closed_on_lock_timeout_without_creating():
    kwargs, spy = _harness(direct=None)
    with pytest.raises(RuntimeError, match="Could not acquire dbo-only line identity create lock"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.resolve_candidate.assert_not_called()
    spy.stamp_identity.assert_not_called()
    spy.rollback_candidate.assert_not_called()


# --- miss: genuine miss under the lock -> create -> stamp --------------------


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_genuine_miss_under_lock_creates_then_stamps_and_returns_the_reread():
    kwargs, spy = _harness(direct=None)
    outcome = run_line_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True
    assert outcome.entity.qbo_id == "3" and outcome.entity.id == 77
    spy.resolve_candidate.assert_called_once_with()
    spy.stamp_identity.assert_called_once_with(spy.resolve_candidate.return_value)
    spy.rollback_candidate.assert_not_called()
    spy.apply.assert_not_called()  # apply_fields is for an EXISTING hit, not a fresh mint
    # The re-read under the lock is the whole point of this path — it must
    # actually run again with the SAME parent-scoped key, not be assumed.
    assert spy.read_direct.call_args_list == [call(19146, "3")] * 2


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_miss_never_adopts_by_side_channel_key():
    """Design §4: create-only. There is no adopt/fingerprint/`_check_no_conflicting`
    step between the lock-confirmed miss and resolve_candidate — the only reads
    are the two parent-scoped direct reads."""
    kwargs, spy = _harness(direct=None)
    run_line_identity_fastpath_dbo_only(**kwargs)
    assert spy.read_direct.call_count == 2
    spy.resolve_candidate.assert_called_once_with()


# --- miss: a racer wins the lock first ----------------------------------------


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_racer_discovered_under_lock_is_adopted_not_duplicated():
    """Two concurrent misses for the same (parent, line): the second MUST adopt
    the racer's row instead of minting a competing one that theft-clear would
    silently orphan."""
    racer_row = SimpleNamespace(id=90, qbo_id="3")
    kwargs, spy = _harness(direct=None)
    spy.read_direct.side_effect = [None, racer_row]
    outcome = run_line_identity_fastpath_dbo_only(**kwargs)
    assert outcome.hit is True and outcome.entity is racer_row
    spy.resolve_candidate.assert_not_called()
    spy.stamp_identity.assert_not_called()
    spy.apply.assert_called_once_with(racer_row)


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_racer_discovered_under_lock_with_apply_returning_none_raises():
    racer_row = SimpleNamespace(id=90, qbo_id="3")
    kwargs, spy = _harness(direct=None)
    spy.read_direct.side_effect = [None, racer_row]
    on_none = Mock()
    kwargs["apply_fields"] = Mock(return_value=None)
    kwargs["on_apply_returned_none"] = on_none
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    on_none.assert_called_once_with(racer_row)
    spy.resolve_candidate.assert_not_called()


# --- miss: the stamp-rollback guarantee (U-354/U-355, now helper-level) -------


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_stamp_raising_rolls_back_the_candidate_and_reraises_the_original():
    kwargs, spy = _harness(direct=None)
    spy.stamp_identity.side_effect = RuntimeError("stamp exploded")
    with pytest.raises(RuntimeError, match="stamp exploded"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.rollback_candidate.assert_called_once_with(spy.resolve_candidate.return_value)


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_rollback_failure_never_masks_the_original_stamp_error():
    kwargs, spy = _harness(direct=None)
    spy.stamp_identity.side_effect = RuntimeError("stamp exploded")
    spy.rollback_candidate.side_effect = RuntimeError("delete also exploded")
    with pytest.raises(RuntimeError, match="stamp exploded"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.rollback_candidate.assert_called_once()


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_stamp_raising_with_no_rollback_wired_still_reraises():
    """rollback_candidate is optional (a caller with nothing to compensate); a
    missing one must never turn a stamp failure into a silent success."""
    kwargs, spy = _harness(direct=None)
    kwargs.pop("rollback_candidate")
    spy.stamp_identity.side_effect = RuntimeError("stamp exploded")
    with pytest.raises(RuntimeError, match="stamp exploded"):
        run_line_identity_fastpath_dbo_only(**kwargs)


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_stamp_that_did_not_land_rolls_back_and_raises():
    """The re-read must carry the identity we resolved. A stamp that silently
    did not write (e.g. the sproc's atomic-pair guard declining without a
    RealmId) would otherwise hand back an unstamped orphan the fast path can
    never find again."""
    kwargs, spy = _harness(direct=None)
    spy.stamp_identity.side_effect = lambda candidate: SimpleNamespace(
        id=candidate.id, public_id=candidate.public_id, qbo_id=None, realm_id=None,
    )
    with pytest.raises(RuntimeError, match="identity stamp did not land"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.rollback_candidate.assert_called_once_with(spy.resolve_candidate.return_value)


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_stamp_landing_a_different_identity_is_also_not_landed():
    kwargs, spy = _harness(direct=None)
    spy.stamp_identity.side_effect = lambda candidate: SimpleNamespace(
        id=candidate.id, public_id=candidate.public_id, qbo_id="4", realm_id="realm-1",
    )
    with pytest.raises(RuntimeError, match="identity stamp did not land"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.rollback_candidate.assert_called_once()


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_stamp_returning_none_raises_race_without_rolling_back():
    """None from stamp_identity means the candidate row is already gone (a
    concurrent delete between create and stamp) — nothing to compensate."""
    kwargs, spy = _harness(direct=None)
    spy.stamp_identity.side_effect = None
    spy.stamp_identity.return_value = None
    with pytest.raises(RuntimeError, match="concurrent write race"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.rollback_candidate.assert_not_called()


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_resolve_candidate_returning_none_raises_without_stamping_or_rolling_back():
    kwargs, spy = _harness(direct=None)
    spy.resolve_candidate.return_value = None
    with pytest.raises(RuntimeError, match="resolve_candidate returned None"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.stamp_identity.assert_not_called()
    spy.rollback_candidate.assert_not_called()


@patch(LOCK_PATCH_TARGET, _granted_lock)
def test_resolve_candidate_raising_propagates_with_nothing_to_roll_back():
    kwargs, spy = _harness(direct=None)
    spy.resolve_candidate.side_effect = RuntimeError("create failed")
    with pytest.raises(RuntimeError, match="create failed"):
        run_line_identity_fastpath_dbo_only(**kwargs)
    spy.stamp_identity.assert_not_called()
    spy.rollback_candidate.assert_not_called()


# --- lock resource key: parent+line scoped, disjoint namespace ----------------


@patch(LOCK_PATCH_TARGET)
def test_lock_resource_key_is_parent_and_line_scoped_in_its_own_namespace(mock_lock):
    """Option A (design §3): keyed on BOTH the parent and the line id — a
    line-id-only key would falsely serialize unrelated parents' line "3"s, a
    parent-only key would not serialize two racers for the same line at all.
    The prefix must never collide with the header helper's
    `qbo_dbo_identity_create:*` or U-304's `qbo_mapping_create:*`."""
    recorded, _recording_lock = _recording_lock_factory()
    mock_lock.side_effect = _recording_lock
    kwargs, _ = _harness(direct=None)
    run_line_identity_fastpath_dbo_only(**kwargs)

    assert recorded == ["qbo_dbo_line_identity_create:BillCreditLineItem:19146:3"]
    assert not recorded[0].startswith("qbo_dbo_identity_create:")
    assert not recorded[0].startswith("qbo_mapping_create:")


@patch(LOCK_PATCH_TARGET)
def test_lock_timeout_is_forwarded(mock_lock):
    seen = {}

    @contextmanager
    def _lock(resource_name, timeout_ms=15000):
        seen["timeout_ms"] = timeout_ms
        yield True

    mock_lock.side_effect = _lock
    kwargs, _ = _harness(direct=None)
    kwargs["lock_timeout_ms"] = 250
    run_line_identity_fastpath_dbo_only(**kwargs)
    assert seen == {"timeout_ms": 250}


# --- the lock is held across the WHOLE critical section ----------------------
#
# Mutation target for the create lock: if the re-read, resolve_candidate, or
# stamp_identity were ever moved outside the `with qbo_app_lock(...)` block,
# this asserted order would break.


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

    def _read_direct(_parent_id, _qbo_line_id):
        call_order.append("read_direct")
        return None

    def _resolve_candidate():
        call_order.append("resolve_candidate")
        return SimpleNamespace(id=77, public_id="pub-77")

    def _stamp_identity(candidate):
        call_order.append("stamp_identity")
        return SimpleNamespace(id=candidate.id, qbo_id="3")

    run_line_identity_fastpath_dbo_only(
        parent_local_id=19146,
        qbo_line_id="3",
        entity_label="BillCreditLineItem",
        external_label="QboVendorCreditLine",
        lock_resource_label="BillCreditLineItem",
        read_direct_by_parent_and_qbo_line_id=_read_direct,
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


@patch(LOCK_PATCH_TARGET)
def test_rollback_runs_inside_the_lock(mock_lock):
    """The compensating delete must not be deferred past lock release: a racer
    admitted after release would re-read the still-present orphan as a HIT and
    update a row this side is about to delete."""
    call_order = []

    @contextmanager
    def _tracking_lock(*_args, **_kwargs):
        call_order.append("lock_acquired")
        try:
            yield True
        finally:
            call_order.append("lock_released")

    mock_lock.side_effect = _tracking_lock
    kwargs, spy = _harness(direct=None)
    spy.stamp_identity.side_effect = RuntimeError("stamp exploded")
    spy.rollback_candidate.side_effect = lambda _c: call_order.append("rollback")
    with pytest.raises(RuntimeError):
        run_line_identity_fastpath_dbo_only(**kwargs)
    assert call_order == ["lock_acquired", "rollback", "lock_released"]
