"""U-740 — redrive script flags for prod gap (a)/(b) expenses."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

_FETCH = "scripts.redrive_skipped_expense_recodes.fetch_candidates"
_GATE = "scripts.redrive_skipped_expense_recodes.recode_write_gate_reason"


def test_fetch_candidates_default_excludes_new_arms():
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    cursor = MagicMock()
    cursor.fetchall.side_effect = [[], [], []]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False

    with patch("scripts.redrive_skipped_expense_recodes.get_connection", return_value=ctx):
        fetch_candidates()

    assert cursor.execute.call_count == 1


def test_fetch_candidates_include_unconfirmed_runs_extra_query():
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False

    with patch("scripts.redrive_skipped_expense_recodes.get_connection", return_value=ctx):
        fetch_candidates(include_unconfirmed=True)

    assert cursor.execute.call_count == 2
    sql_second = cursor.execute.call_args_list[1][0][0]
    assert "unconfirmed-suggested" in sql_second
    assert re.search(r"N'suggested'", sql_second, re.IGNORECASE)


def test_fetch_candidates_include_completed_unsubmitted_runs_extra_query():
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False

    with patch("scripts.redrive_skipped_expense_recodes.get_connection", return_value=ctx):
        fetch_candidates(include_completed_unsubmitted=True)

    assert cursor.execute.call_count == 2
    sql_second = cursor.execute.call_args_list[1][0][0]
    assert "completed-unsubmitted" in sql_second
    assert re.search(r"N'completed'", sql_second, re.IGNORECASE)
    assert "NOT EXISTS" in sql_second.upper()
    assert re.search(r"DBO\.\[REVIEW\]", sql_second, re.IGNORECASE)


def test_main_passes_flags_to_fetch(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    with patch(_FETCH, return_value=[]) as mock_fetch, patch(_GATE, return_value=None):
        rc = main(
            [
                "--include-unconfirmed",
                "--include-completed-unsubmitted",
            ]
        )

    assert rc == 0
    mock_fetch.assert_called_once()
    kwargs = mock_fetch.call_args.kwargs
    assert kwargs["include_unconfirmed"] is True
    assert kwargs["include_completed_unsubmitted"] is True
    out = capsys.readouterr().out
    assert "DRY RUN" in out or "dry-run" in out.lower()


def test_unconfirmed_sql_uses_claim_lease_predicate():
    from scripts.redrive_skipped_expense_recodes import UNCONFIRMED_CANDIDATE_SQL

    assert "ClaimedByUserId" in UNCONFIRMED_CANDIDATE_SQL
    assert "ClaimedAt" in UNCONFIRMED_CANDIDATE_SQL
    assert "DATEADD(SECOND, -900" in UNCONFIRMED_CANDIDATE_SQL


def test_unconfirmed_sql_requires_itemrefvalue_null_on_exists():
    from scripts.redrive_skipped_expense_recodes import UNCONFIRMED_CANDIDATE_SQL

    purchase_exists_blocks = re.findall(
        r"AND\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+qbo\.\[Purchase\]\s+p\b(.*?)\)\s*(?:\n|\{)",
        UNCONFIRMED_CANDIDATE_SQL,
        re.IGNORECASE | re.DOTALL,
    )
    assert purchase_exists_blocks
    for body in purchase_exists_blocks:
        assert re.search(r"ItemRefValue\]\s+IS\s+NULL", body, re.IGNORECASE)
