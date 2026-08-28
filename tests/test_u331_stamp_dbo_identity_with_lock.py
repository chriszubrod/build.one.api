"""
U-331 — the shared stamp-lock helper (base/identity_fastpath.py::stamp_dbo_identity_with_lock).

Design doc: docs/design/stamp-lock-helper.md (U-328). Mechanically extracts the
"stamp a QBO identity onto a candidate row under a row-scoped app lock" shape
hand-copied across all 6 `run_identity_fastpath_dbo_only` MISS-branch adopters
(Attachment/U-300b, CostCode+SubCostCode/U-307c, Customer/U-310, Project/U-311,
Vendor/U-313) into one function, closing two latent gaps as part of the
extraction (D1: CostCode/SubCostCode's missing ROWVERSION-race guard on the
pre-identity field write; D2: Attachment/CostCode/SubCostCode not recording a
ReconciliationIssue on a stamp-time theft-guard trip).

These tests pin the helper's own contract in isolation — no DB/QBO I/O;
`qbo_app_lock` is always patched, per the house convention
`test_u300a_identity_fastpath_dbo_only.py` established for this module's
other lock-based helper.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.base.identity_fastpath import stamp_dbo_identity_with_lock

LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"


@contextmanager
def _granted_lock(*_args, **_kwargs):
    yield True


@contextmanager
def _denied_lock(*_args, **_kwargs):
    yield False


def _recording_lock_factory(recorded):
    @contextmanager
    def _lock(resource_name, timeout_ms=15000):
        recorded.append((resource_name, timeout_ms))
        yield True

    return _lock


def _row(**overrides):
    defaults = dict(id=150, qbo_id=None, realm_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _harness(*, read_by_id_returns, **overrides):
    """Build a stamp_dbo_identity_with_lock kwargs dict plus the Mocks it drives."""
    spy = SimpleNamespace(
        read_by_id=Mock(side_effect=read_by_id_returns if callable(read_by_id_returns) else lambda _id: read_by_id_returns),
        write_identity=Mock(),
        apply_fields=None,
        on_conflict=None,
    )
    kwargs = dict(
        candidate_id=150,
        entity_label="CostCode",
        qbo_id="Q-99",
        realm_id="realm-1",
        read_by_id=spy.read_by_id,
        write_identity=spy.write_identity,
    )
    kwargs.update(overrides)
    return kwargs, spy


# --- lock-acquire timeout: fail closed ---------------------------------------


def test_lock_timeout_raises_and_never_reads_or_writes():
    kwargs, spy = _harness(read_by_id_returns=_row())
    with patch(LOCK_PATCH_TARGET, side_effect=_denied_lock):
        with pytest.raises(RuntimeError, match="Could not acquire identity-stamp lock"):
            stamp_dbo_identity_with_lock(**kwargs)
    spy.read_by_id.assert_not_called()
    spy.write_identity.assert_not_called()


def test_lock_timeout_forwards_custom_lock_timeout_ms():
    recorded = []
    kwargs, _ = _harness(read_by_id_returns=_row(), lock_timeout_ms=5000)
    with patch(LOCK_PATCH_TARGET, side_effect=_recording_lock_factory(recorded)):
        stamp_dbo_identity_with_lock(**kwargs)
    assert recorded == [("qbo_dbo_identity_stamp:CostCode:150", 5000)]


# --- candidate vanished under the lock (concurrent delete) -------------------


def test_current_none_returns_none_without_writing():
    kwargs, spy = _harness(read_by_id_returns=None)
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        result = stamp_dbo_identity_with_lock(**kwargs)
    assert result is None
    spy.write_identity.assert_not_called()


# --- theft-guard ---------------------------------------------------------------


def test_theft_guard_raises_and_calls_on_conflict_first():
    calls = []
    on_conflict = Mock(side_effect=lambda c: calls.append(("on_conflict", c.qbo_id)))
    conflicting = _row(qbo_id="Q-OTHER", realm_id="realm-1")
    kwargs, spy = _harness(read_by_id_returns=conflicting, on_conflict=on_conflict)
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries QBO identity Q-OTHER"):
            stamp_dbo_identity_with_lock(**kwargs)
    on_conflict.assert_called_once_with(conflicting)
    spy.write_identity.assert_not_called()


def test_theft_guard_raises_even_without_on_conflict():
    conflicting = _row(qbo_id="Q-OTHER", realm_id="realm-1")
    kwargs, spy = _harness(read_by_id_returns=conflicting)
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries QBO identity Q-OTHER"):
            stamp_dbo_identity_with_lock(**kwargs)
    spy.write_identity.assert_not_called()


def test_theft_guard_catches_same_qbo_id_different_realm():
    """QBO ids are only unique WITHIN a realm -- a QboId-only check would let a
    same-QboId-different-realm row through."""
    conflicting = _row(qbo_id="Q-99", realm_id="realm-OTHER")
    kwargs, spy = _harness(read_by_id_returns=conflicting)
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries QBO identity Q-99"):
            stamp_dbo_identity_with_lock(**kwargs)
    spy.write_identity.assert_not_called()


def test_benign_reresolve_to_exact_same_identity_proceeds_without_raising():
    already_stamped = _row(qbo_id="Q-99", realm_id="realm-1")
    kwargs, spy = _harness(read_by_id_returns=already_stamped)
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        result = stamp_dbo_identity_with_lock(**kwargs)
    spy.write_identity.assert_called_once_with(already_stamped)
    assert result is already_stamped  # final re-read via the same read_by_id stub


def test_no_existing_identity_proceeds_without_raising():
    unmapped = _row(qbo_id=None, realm_id=None)
    kwargs, spy = _harness(read_by_id_returns=unmapped)
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        stamp_dbo_identity_with_lock(**kwargs)
    spy.write_identity.assert_called_once_with(unmapped)


# --- apply_fields: the D1 ROWVERSION-race guard -------------------------------


def test_apply_fields_returning_none_raises_concurrent_write_race_and_never_writes_identity():
    """The gap D1 (docs/design/stamp-lock-helper.md) flags: CostCode/SubCostCode's
    hand-copies discarded update_by_id's return value entirely, so a concurrent
    ROWVERSION race silently succeeded at set_qbo_identity anyway. This guard is
    what closes it structurally for every adopter, not just the two that needed it."""
    unmapped = _row(qbo_id=None, realm_id=None)
    kwargs, spy = _harness(
        read_by_id_returns=unmapped, apply_fields=Mock(return_value=None),
    )
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        with pytest.raises(RuntimeError, match="concurrent write race"):
            stamp_dbo_identity_with_lock(**kwargs)
    spy.write_identity.assert_not_called()


def test_apply_fields_success_is_called_before_write_identity_with_current_row():
    unmapped = _row(qbo_id=None, realm_id=None)
    order = []
    apply_fields = Mock(side_effect=lambda c: (order.append("apply_fields"), c)[-1])
    kwargs, spy = _harness(read_by_id_returns=unmapped, apply_fields=apply_fields)
    spy.write_identity.side_effect = lambda c: order.append("write_identity")
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        stamp_dbo_identity_with_lock(**kwargs)
    apply_fields.assert_called_once_with(unmapped)
    spy.write_identity.assert_called_once_with(unmapped)
    assert order == ["apply_fields", "write_identity"]


def test_apply_fields_omitted_writes_identity_on_current_row_unchanged():
    unmapped = _row(qbo_id=None, realm_id=None)
    kwargs, spy = _harness(read_by_id_returns=unmapped)
    assert "apply_fields" not in kwargs  # default None
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        stamp_dbo_identity_with_lock(**kwargs)
    spy.write_identity.assert_called_once_with(unmapped)


def test_theft_guard_runs_before_apply_fields():
    """The loser of a stamp race must never touch the row's fields -- the
    theft-guard must short-circuit before apply_fields is ever called."""
    conflicting = _row(qbo_id="Q-OTHER", realm_id="realm-1")
    apply_fields = Mock()
    kwargs, _ = _harness(read_by_id_returns=conflicting, apply_fields=apply_fields)
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError):
            stamp_dbo_identity_with_lock(**kwargs)
    apply_fields.assert_not_called()


# --- happy path: final re-read ------------------------------------------------


def test_happy_path_returns_final_reread_not_the_apply_fields_result():
    unmapped = _row(id=150, qbo_id=None, realm_id=None)
    refreshed = _row(id=150, qbo_id="Q-99", realm_id="realm-1")
    read_sequence = iter([unmapped, refreshed])
    kwargs, spy = _harness(read_by_id_returns=lambda _id: next(read_sequence))
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock):
        result = stamp_dbo_identity_with_lock(**kwargs)
    assert result is refreshed
    assert spy.read_by_id.call_count == 2
