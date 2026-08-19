"""Pure-logic tests for the U-275 QboActive mirror backfill script."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.backfill_qbo_active_mirror import (
    SPECS_BY_KEY,
    backfill_entity,
    main as backfill_main,
)


def _connection_with_pending_sequence(pending_then_remaining):
    """Build a get_connection() mock whose cursor.fetchone()[0] yields each
    value in pending_then_remaining in order across successive _pending_sql
    calls (dry-run/pre-check, post-update re-check, ...)."""
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(n,) for n in pending_then_remaining]
    cursor.rowcount = pending_then_remaining[0] if pending_then_remaining else 0
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx, cursor, conn


@patch("scripts.backfill_qbo_active_mirror.get_connection")
def test_dry_run_never_issues_update(mock_get_connection):
    """--apply not passed: only the pending-count SELECT runs, never the UPDATE."""
    ctx, cursor, conn = _connection_with_pending_sequence([3])
    mock_get_connection.return_value = ctx

    ok = backfill_entity(SPECS_BY_KEY["vendor"], apply=False)

    assert ok is True
    assert cursor.execute.call_count == 1
    executed_sql = cursor.execute.call_args[0][0]
    assert "UPDATE" not in executed_sql.upper()
    conn.commit.assert_not_called()


@patch("scripts.backfill_qbo_active_mirror.get_connection")
def test_apply_with_pending_rows_updates_then_reverifies_zero_remaining(mock_get_connection):
    """--apply with pending>0: UPDATE runs once, commits, then re-checks for zero remaining."""
    ctx, cursor, conn = _connection_with_pending_sequence([5, 0])
    mock_get_connection.return_value = ctx

    ok = backfill_entity(SPECS_BY_KEY["payment_term"], apply=True)

    assert ok is True
    executed = [call.args[0].upper() for call in cursor.execute.call_args_list]
    assert any("UPDATE" in sql for sql in executed)
    assert any("SELECT COUNT" in sql for sql in executed)
    conn.commit.assert_called_once()


@patch("scripts.backfill_qbo_active_mirror.get_connection")
def test_apply_with_zero_pending_skips_update(mock_get_connection):
    """--apply with nothing pending: no UPDATE issued, no commit, still verifies clean."""
    ctx, cursor, conn = _connection_with_pending_sequence([0, 0])
    mock_get_connection.return_value = ctx

    ok = backfill_entity(SPECS_BY_KEY["sub_cost_code"], apply=True)

    assert ok is True
    executed = [call.args[0].upper() for call in cursor.execute.call_args_list]
    assert not any("UPDATE" in sql for sql in executed)
    conn.commit.assert_not_called()


@patch("scripts.backfill_qbo_active_mirror.get_connection")
def test_apply_reports_failure_when_mismatches_remain_after_update(mock_get_connection):
    """A remaining mismatch after UPDATE (e.g. concurrent pull mid-backfill) fails verification, not silently."""
    ctx, cursor, conn = _connection_with_pending_sequence([5, 2])
    mock_get_connection.return_value = ctx
    cursor.fetchall.return_value = [
        MagicMock(Id=1, QboActive=True, StagingActive=False),
    ]

    ok = backfill_entity(SPECS_BY_KEY["vendor"], apply=True)

    assert ok is False


@patch("scripts.backfill_qbo_active_mirror.assert_cli_system_admin")
@patch("scripts.backfill_qbo_active_mirror.backfill_entity", return_value=False)
def test_main_returns_nonzero_on_entity_failure(mock_backfill, mock_admin):
    with patch("sys.argv", ["backfill_qbo_active_mirror.py", "--entity", "vendor"]):
        assert backfill_main() == 1
    mock_backfill.assert_called_once()


@patch("scripts.backfill_qbo_active_mirror.assert_cli_system_admin")
@patch("scripts.backfill_qbo_active_mirror.backfill_entity", return_value=True)
def test_main_returns_zero_when_all_entities_pass(mock_backfill, mock_admin):
    with patch("sys.argv", ["backfill_qbo_active_mirror.py"]):
        assert backfill_main() == 0
    assert mock_backfill.call_count == len(SPECS_BY_KEY)


def test_specs_filtered_to_exactly_the_three_active_mirror_entities():
    """SPECS_BY_KEY is REFERENCE_ENTITY_SPECS filtered by key — guards the filter itself,
    not the entity topology (that's covered by
    test_qbo_identity_reference.py::test_reference_entity_specs_topology)."""
    assert set(SPECS_BY_KEY.keys()) == {"vendor", "payment_term", "sub_cost_code"}
