"""U-652d5 one-time backfill: stamp dbo.CodingSuggestion.ExpenseId for expense-target rows.

Runs the same guards as StampCodingSuggestionExpenseId, across ALL rows in one set-based UPDATE:
  - TargetKind = N'expense' only (bill rows carry no ExpenseId; a CHECK constraint forces it NULL)
  - ExpenseId IS NULL, or pointing at a missing dbo.Expense (dangling)
  - joined to dbo.Expense on QboId + RealmId (the purchase identity)
  - never sets an ExpenseId another suggestion row already holds (UQ_CodingSuggestion_ExpenseId)

Safety:
  - Dry-run by default: SELECTs only, prints the counts.
  - --apply EXECs dbo.HealCodingSuggestionExpenseIds in batches until it heals 0, capped at 20 batches
    (single source of the write; the script carries no UPDATE text), re-prints the counts, and exits 1 when
    NULL rows whose purchase has an expense OR healable dangling ExpenseIds (a matching expense exists) remain.
    Dangling ids with no matching expense are reported and do not fail the run.
  - Prod data write: run at Gate 2 only, after reviewing the dry-run.

Usage:
  .venv/bin/python scripts/backfill_coding_suggestion_expense_id.py           # dry-run
  .venv/bin/python scripts/backfill_coding_suggestion_expense_id.py --apply   # write
"""

# Python Standard Library Imports
import argparse
import sys

# Local Imports
sys.path.insert(0, ".")
from scripts.sync_helper import assert_cli_system_admin  # noqa: E402
from shared.database import get_connection  # noqa: E402

COUNT_NULL_EXPENSE_TARGET = """
SELECT COUNT(*) AS [N]
FROM dbo.[CodingSuggestion] cs
WHERE cs.[TargetKind] = N'expense'
  AND cs.[ExpenseId] IS NULL;
"""

COUNT_NULL_WITH_EXPENSE = """
SELECT COUNT(DISTINCT cs.[Id]) AS [N]
FROM dbo.[CodingSuggestion] cs
INNER JOIN dbo.[Expense] e
    ON e.[QboId] = cs.[QboPurchaseQboId]
   AND e.[RealmId] = cs.[RealmId]
WHERE cs.[TargetKind] = N'expense'
  AND cs.[ExpenseId] IS NULL;
"""

COUNT_DANGLING_WITH_EXPENSE = """
SELECT COUNT(DISTINCT cs.[Id]) AS [N]
FROM dbo.[CodingSuggestion] cs
INNER JOIN dbo.[Expense] e
    ON e.[QboId] = cs.[QboPurchaseQboId]
   AND e.[RealmId] = cs.[RealmId]
WHERE cs.[TargetKind] = N'expense'
  AND cs.[ExpenseId] IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM dbo.[Expense] e0 WHERE e0.[Id] = cs.[ExpenseId]
  );
"""

COUNT_DANGLING = """
SELECT COUNT(*) AS [N]
FROM dbo.[CodingSuggestion] cs
WHERE cs.[TargetKind] = N'expense'
  AND cs.[ExpenseId] IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM dbo.[Expense] e0 WHERE e0.[Id] = cs.[ExpenseId]
  );
"""

HEAL_BATCH = 500
HEAL_BATCH_CAP = 20  # batches per --apply run; re-run to continue


def _count(cursor, sql: str) -> int:
    cursor.execute(sql)
    row = cursor.fetchone()
    return int(row.N) if row is not None else 0


def count_report(cursor) -> dict:
    return {
        "null_expense_target": _count(cursor, COUNT_NULL_EXPENSE_TARGET),
        "null_with_expense": _count(cursor, COUNT_NULL_WITH_EXPENSE),
        "dangling": _count(cursor, COUNT_DANGLING),
        # healable: a dangling id whose purchase still has an expense (fails the run); the rest are informational
        "dangling_with_expense": _count(cursor, COUNT_DANGLING_WITH_EXPENSE),
    }


def _print_report(title: str, report: dict) -> None:
    print(title)
    print(f"  expense-target rows with NULL ExpenseId:              {report['null_expense_target']}")
    print(f"  ...of those, purchase has a matching dbo.Expense:     {report['null_with_expense']}")
    print(f"  dangling ExpenseIds (points at a missing Expense):    {report['dangling']}")
    print(f"  ...of those, purchase still has a matching Expense:   {report['dangling_with_expense']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill CodingSuggestion.ExpenseId (U-652d5).")
    parser.add_argument("--apply", action="store_true", help="run the UPDATE (default: dry-run)")
    args = parser.parse_args(argv)

    with get_connection() as conn:
        cursor = conn.cursor()
        before = count_report(cursor)
        if not args.apply:
            _print_report("DRY RUN (no writes). Counts before:", before)
            return 0

        _print_report("Counts before apply:", before)
        healed_total = 0
        batches = 0
        cap_hit = False
        while True:
            cursor.execute("EXEC dbo.HealCodingSuggestionExpenseIds @Limit=?", (HEAL_BATCH,))
            batches += 1
            healed = int(cursor.fetchone().Healed)
            healed_total += healed
            if healed == 0:
                break
            if batches >= HEAL_BATCH_CAP:
                cap_hit = True
                break
        print(f"Healed {healed_total} row(s) in {batches} batch(es) via dbo.HealCodingSuggestionExpenseIds.")
        if cap_hit:
            print(f"CAP: stopped at {HEAL_BATCH_CAP} batches with rows still healing. Re-run --apply to continue.")
        after = count_report(cursor)

    _print_report("Counts after apply:", after)
    failed = after["null_with_expense"] > 0 or after["dangling_with_expense"] > 0
    if failed:
        print(
            f"FAIL: {after['null_with_expense']} NULL row(s) with a purchase expense and "
            f"{after['dangling_with_expense']} healable dangling ExpenseId(s) remain. "
            "Do not apply the simplified reads until both are 0."
        )
        return 1
    print("OK: NULL rows whose purchase has an expense = 0 and no healable dangling ExpenseIds.")
    if after["dangling"]:
        print(f"NOTE: {after['dangling']} dangling ExpenseId(s) have no matching expense; reported, not failing.")
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    sys.exit(main())
