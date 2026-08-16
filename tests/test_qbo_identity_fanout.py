"""Tests for U-238c fan-out overlap enforcement helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base import identity_fanout


def _cursor_with_rows(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    return cursor


def test_check_customer_fanout_overlap_pass():
    cursor = _cursor_with_rows([])
    ok, rows = identity_fanout.check_customer_fanout_overlap(cursor)
    assert ok is True
    assert rows == []
    cursor.execute.assert_called_once_with(identity_fanout.CUSTOMER_FANOUT_OVERLAP_SQL)


def test_check_customer_fanout_overlap_detected():
    cursor = _cursor_with_rows(
        [MagicMock(QboId="1", RealmId="realm-a"), MagicMock(QboId="2", RealmId="realm-b")]
    )
    ok, rows = identity_fanout.check_customer_fanout_overlap(cursor)
    assert ok is False
    assert rows == [("1", "realm-a"), ("2", "realm-b")]


def test_check_customer_fanout_overlap_truncates_error_logging():
    rows = [MagicMock(QboId=str(i), RealmId=f"realm-{i}") for i in range(7)]
    cursor = _cursor_with_rows(rows)
    with patch.object(identity_fanout.logger, "error") as mock_error:
        ok, overlap_rows = identity_fanout.check_customer_fanout_overlap(cursor)
    assert ok is False
    assert len(overlap_rows) == 7
    assert mock_error.call_count == 6
    last_call = mock_error.call_args_list[-1]
    assert "... and %s more pair(s)" in last_call.args[0]
    assert last_call.args[1] == 2


def test_check_item_fanout_overlap_detected():
    cursor = _cursor_with_rows([MagicMock(QboId="99", RealmId="realm-x")])
    ok, rows = identity_fanout.check_item_fanout_overlap(cursor)
    assert ok is False
    assert rows == [("99", "realm-x")]


def test_check_all_fanout_overlaps_short_circuits_without_connection():
    with patch("integrations.intuit.qbo.base.identity_fanout.get_connection") as mock_conn:
        result = identity_fanout.check_all_fanout_overlaps(use_connection=False)
    assert result == {"customer_project": True, "cost_code_sub_cost_code": True}
    mock_conn.assert_not_called()


def test_check_all_fanout_overlaps_runs_both_checks():
    cursor = MagicMock()
    conn = MagicMock()
    conn.__enter__.return_value.cursor.return_value = cursor
    with patch("integrations.intuit.qbo.base.identity_fanout.get_connection", return_value=conn), patch(
        "integrations.intuit.qbo.base.identity_fanout.check_customer_fanout_overlap",
        return_value=(True, []),
    ) as mock_customer, patch(
        "integrations.intuit.qbo.base.identity_fanout.check_item_fanout_overlap",
        return_value=(False, [("1", "r")]),
    ) as mock_item:
        result = identity_fanout.check_all_fanout_overlaps(use_connection=True)
    assert result == {"customer_project": True, "cost_code_sub_cost_code": False}
    mock_customer.assert_called_once_with(cursor)
    mock_item.assert_called_once_with(cursor)
