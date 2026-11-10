"""U-652d5 — one-time backfill of CodingSuggestion.ExpenseId (scripts/backfill_coding_suggestion_expense_id.py).

Pure tests: the DB connection is mocked. Pins the set-based UPDATE text, the dry-run is SELECT-only, and
--apply exits non-zero when NULL rows whose purchase has an expense remain after the write.
"""

from __future__ import annotations

import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.sproc_text import REPO_ROOT

SCRIPT = "scripts.backfill_coding_suggestion_expense_id"


def _mod():
    import importlib

    return importlib.import_module(SCRIPT)


def _conn(counts: list[int], rowcount: int = 0):
    """A mocked get_connection() context. `counts` feeds the fetchone() of each count query in order
    (null_total, null_with_expense, dangling) for each report, `rowcount` is the UPDATE's rowcount."""
    cursor = MagicMock()
    cursor.rowcount = rowcount
    cursor.fetchone.side_effect = [SimpleNamespace(N=n) for n in counts]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm, conn, cursor


def _executed(cursor) -> list[str]:
    return [c.args[0] for c in cursor.execute.call_args_list]


# --- SQL text --------------------------------------------------------------------------------


def test_apply_execs_the_heal_proc_and_carries_no_update_text():
    m = _mod()
    src = (REPO_ROOT / "scripts/backfill_coding_suggestion_expense_id.py").read_text()
    assert "EXEC dbo.HealCodingSuggestionExpenseIds" in src
    assert "UPDATE dbo" not in src and "UPDATE cs" not in src
    assert not hasattr(m, "APPLY_SQL")


def test_every_count_query_is_read_only_and_expense_scoped():
    m = _mod()
    for sql in (m.COUNT_NULL_EXPENSE_TARGET, m.COUNT_NULL_WITH_EXPENSE, m.COUNT_DANGLING):
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert "UPDATE" not in upper and "DELETE" not in upper and "INSERT" not in upper
        assert "TargetKind" in sql


def test_null_with_expense_joins_on_qbo_identity():
    m = _mod()
    assert re.search(r"e\.\[QboId\]\s*=\s*cs\.\[QboPurchaseQboId\]", m.COUNT_NULL_WITH_EXPENSE)
    assert re.search(r"e\.\[RealmId\]\s*=\s*cs\.\[RealmId\]", m.COUNT_NULL_WITH_EXPENSE)


def _seq(*items):
    """Shape a fetchone sequence: ('N', n) for count rows, ('Healed', n) for heal batches."""
    return [SimpleNamespace(**{k: v}) for k, v in items]


def _count_rows(*ns):
    return [("N", n) for n in ns]


# --- dry run ---------------------------------------------------------------------------------


def test_dry_run_issues_only_selects_and_reports_counts(capsys):
    m = _mod()
    cm, conn, cursor = _conn(counts=[332, 1, 0, 0])
    with patch(f"{SCRIPT}.get_connection", return_value=cm):
        rc = m.main([])
    assert rc == 0
    executed = _executed(cursor)
    assert executed, "dry run must query"
    assert all(s.lstrip().upper().startswith("SELECT") for s in executed)
    out = capsys.readouterr().out
    assert "332" in out and "dry run" in out.lower()


# --- apply -----------------------------------------------------------------------------------


def _apply_conn(before, heals, after):
    cm, conn, cursor = _conn(counts=[])
    cursor.fetchone.side_effect = (
        [SimpleNamespace(N=n) for n in before]
        + [SimpleNamespace(Healed=h) for h in heals]
        + [SimpleNamespace(N=n) for n in after]
    )
    return cm, conn, cursor


def test_apply_drains_the_heal_proc_until_zero_and_passes(capsys):
    m = _mod()
    cm, conn, cursor = _apply_conn(before=[1, 1, 0, 0], heals=[1, 0], after=[0, 0, 0, 0])
    with patch(f"{SCRIPT}.get_connection", return_value=cm):
        rc = m.main(["--apply"])
    assert rc == 0
    heal_calls = [s for s in _executed(cursor) if "HealCodingSuggestionExpenseIds" in s]
    assert len(heal_calls) == 2, "one EXEC per batch until a batch heals 0"
    assert "Healed 1 row(s) in 2 batch(es)" in capsys.readouterr().out


def test_apply_caps_the_loop_at_twenty_batches_and_says_so(capsys):
    m = _mod()
    cm, conn, cursor = _apply_conn(before=[0, 0, 0, 0], heals=[1] * 20, after=[0, 0, 0, 0])
    with patch(f"{SCRIPT}.get_connection", return_value=cm):
        m.main(["--apply"])
    heal_calls = [s for s in _executed(cursor) if "HealCodingSuggestionExpenseIds" in s]
    assert len(heal_calls) == 20
    assert "CAP: stopped at 20 batches" in capsys.readouterr().out


def test_apply_exits_non_zero_when_a_null_row_with_an_expense_remains():
    m = _mod()
    cm, conn, cursor = _apply_conn(before=[3, 3, 0, 0], heals=[0], after=[1, 2, 0, 0])
    with patch(f"{SCRIPT}.get_connection", return_value=cm):
        assert m.main(["--apply"]) != 0


def test_apply_exits_non_zero_when_a_healable_dangling_id_remains():
    m = _mod()
    cm, conn, cursor = _apply_conn(before=[0, 0, 0, 1], heals=[0], after=[0, 0, 0, 1])
    with patch(f"{SCRIPT}.get_connection", return_value=cm):
        assert m.main(["--apply"]) != 0


def test_apply_passes_with_dangling_ids_that_have_no_matching_expense(capsys):
    m = _mod()
    cm, conn, cursor = _apply_conn(before=[0, 0, 2, 0], heals=[0], after=[0, 0, 2, 0])
    with patch(f"{SCRIPT}.get_connection", return_value=cm):
        rc = m.main(["--apply"])
    assert rc == 0
    assert "NOTE: 2 dangling" in capsys.readouterr().out


def test_dry_run_never_writes_even_with_remaining_rows():
    m = _mod()
    cm, conn, cursor = _conn(counts=[3, 3, 0, 0])
    with patch(f"{SCRIPT}.get_connection", return_value=cm):
        rc = m.main([])
    assert rc == 0
    assert not any("HealCodingSuggestionExpenseIds" in s for s in _executed(cursor))


def test_script_declares_cli_system_admin_under_main():
    src = (REPO_ROOT / "scripts/backfill_coding_suggestion_expense_id.py").read_text()
    assert re.search(r'if __name__ == "__main__":\s*\n\s+assert_cli_system_admin\(\)', src)
