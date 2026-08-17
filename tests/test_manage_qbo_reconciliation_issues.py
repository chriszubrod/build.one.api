"""Pure-logic tests for manage_qbo_reconciliation_issues helpers (U-246 fix round)."""
from argparse import Namespace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.reconciliation.business.model import ReconciliationIssue
from scripts.manage_qbo_reconciliation_issues import (
    _repeat_analysis_by_drift_type,
    _utc_cutoff,
    _validate_max_rows,
    cmd_acknowledge,
    cmd_bulk_resolve,
)


def test_utc_cutoff_returns_naive_datetime():
    cutoff = _utc_cutoff(7)
    assert cutoff.tzinfo is None


def test_utc_cutoff_approximately_seven_days_ago():
    now = datetime.utcnow()
    cutoff = _utc_cutoff(7)
    delta = now - cutoff
    assert timedelta(days=6, hours=23) <= delta <= timedelta(days=7, minutes=1)


def test_utc_cutoff_rejects_negative_days():
    with pytest.raises(SystemExit) as exc_info:
        _utc_cutoff(-5)
    assert exc_info.value.code == 2


@pytest.mark.parametrize("bad_value", [5001, 0, -1])
def test_validate_max_rows_rejects_invalid(bad_value):
    with pytest.raises(SystemExit) as exc_info:
        _validate_max_rows(bad_value)
    assert exc_info.value.code == 2


def test_validate_max_rows_accepts_ceiling():
    assert _validate_max_rows(5000) == 5000


def test_validate_max_rows_accepts_below_ceiling():
    assert _validate_max_rows(100) == 100


def test_cmd_acknowledge_already_acknowledged_is_no_op(capsys):
    issue_id = 42
    result = ReconciliationIssue(
        id=issue_id,
        status="acknowledged",
        acknowledged_at="2026-08-01 12:00:00",
    )
    mock_repo = MagicMock()
    mock_repo.acknowledge.return_value = result

    with patch(
        "scripts.manage_qbo_reconciliation_issues._read_issue_status",
        return_value="acknowledged",
    ), patch(
        "scripts.manage_qbo_reconciliation_issues.ReconciliationIssueRepository",
        return_value=mock_repo,
    ):
        exit_code = cmd_acknowledge(Namespace(id=issue_id))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert f"no-op: Id={issue_id} already acknowledged" in captured.out
    mock_repo.acknowledge.assert_called_once_with(issue_id)


def test_cmd_bulk_resolve_dry_run_uses_preview_not_bulk_resolve(capsys):
    mock_repo = MagicMock()
    mock_repo.preview_bulk_resolve.return_value = [
        {
            "id": 10,
            "drift_type": "pull_delete_reconcile",
            "entity_type": "Bill",
            "qbo_id": "QBO-10",
            "created_datetime": "2026-01-01 00:00:00",
            "total_match_count": 15,
        }
    ]

    args = Namespace(
        drift_type="pull_delete_reconcile",
        entity_type=None,
        created_before_days=None,
        realm_id=None,
        status="open",
        max_rows=1000,
        apply=False,
    )

    with patch(
        "scripts.manage_qbo_reconciliation_issues.ReconciliationIssueRepository",
        return_value=mock_repo,
    ):
        exit_code = cmd_bulk_resolve(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    mock_repo.preview_bulk_resolve.assert_called_once_with(
        drift_type="pull_delete_reconcile",
        entity_type=None,
        created_before=None,
        realm_id=None,
        status="open",
        max_rows=1000,
    )
    mock_repo.bulk_resolve.assert_not_called()
    assert "Matched 15 row(s)" in captured.out
    assert "Id=    10" in captured.out
    assert "DRY-RUN: no rows modified" in captured.out


def test_cmd_bulk_resolve_apply_calls_bulk_resolve(capsys):
    mock_repo = MagicMock()
    mock_repo.preview_bulk_resolve.return_value = [
        {
            "id": 10,
            "drift_type": "pull_delete_reconcile",
            "entity_type": "Bill",
            "qbo_id": "QBO-10",
            "created_datetime": "2026-01-01 00:00:00",
            "total_match_count": 1,
        }
    ]
    mock_repo.bulk_resolve.return_value = [10]

    args = Namespace(
        drift_type="pull_delete_reconcile",
        entity_type="Bill",
        created_before_days=30,
        realm_id="realm-1",
        status="open",
        max_rows=500,
        apply=True,
    )

    with patch(
        "scripts.manage_qbo_reconciliation_issues.ReconciliationIssueRepository",
        return_value=mock_repo,
    ), patch(
        "scripts.manage_qbo_reconciliation_issues._utc_cutoff",
        return_value=datetime(2026, 1, 1, 0, 0, 0),
    ):
        exit_code = cmd_bulk_resolve(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    expected_kwargs = {
        "drift_type": "pull_delete_reconcile",
        "entity_type": "Bill",
        "created_before": datetime(2026, 1, 1, 0, 0, 0),
        "realm_id": "realm-1",
        "status": "open",
        "max_rows": 500,
    }
    mock_repo.preview_bulk_resolve.assert_called_once_with(**expected_kwargs)
    mock_repo.bulk_resolve.assert_called_once_with(**expected_kwargs)
    assert "Resolved 1 row(s)." in captured.out


def test_cmd_bulk_resolve_no_matches(capsys):
    mock_repo = MagicMock()
    mock_repo.preview_bulk_resolve.return_value = []

    args = Namespace(
        drift_type="orphaned_item_scc_mapping",
        entity_type=None,
        created_before_days=None,
        realm_id=None,
        status="open",
        max_rows=1000,
        apply=False,
    )

    with patch(
        "scripts.manage_qbo_reconciliation_issues.ReconciliationIssueRepository",
        return_value=mock_repo,
    ):
        exit_code = cmd_bulk_resolve(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Matched 0 row(s)" in captured.out
    assert "DRY-RUN: no rows modified" in captured.out


def test_repeat_analysis_by_drift_type_single_query_and_grouping(capsys):
    cutoff = datetime.utcnow() - timedelta(days=7)
    stale_last_seen = cutoff - timedelta(days=1)
    active_last_seen = cutoff + timedelta(days=1)

    mock_rows = [
        SimpleNamespace(
            DriftType="type_a",
            EntityType="Bill",
            QboId="1",
            RepCount=3,
            LastSeen=active_last_seen,
        ),
        SimpleNamespace(
            DriftType="type_a",
            EntityType="Bill",
            QboId="2",
            RepCount=1,
            LastSeen=stale_last_seen,
        ),
        SimpleNamespace(
            DriftType="type_b",
            EntityType="Invoice",
            QboId="9",
            RepCount=5,
            LastSeen=active_last_seen,
        ),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = mock_rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch(
        "scripts.manage_qbo_reconciliation_issues.get_connection",
        return_value=mock_conn,
    ), patch(
        "scripts.manage_qbo_reconciliation_issues._utc_cutoff",
        return_value=cutoff,
    ):
        _repeat_analysis_by_drift_type(7)

    mock_cursor.execute.assert_called_once()
    captured = capsys.readouterr()
    assert "type_a" in captured.out
    assert "type_b" in captured.out
    assert "Repeat-count analysis by DriftType" in captured.out
    # type_a: 4 total rows, 2 unique keys, max reps 3, 1 active + 1 stale
    assert "         4           2" in captured.out or "4           2" in captured.out
