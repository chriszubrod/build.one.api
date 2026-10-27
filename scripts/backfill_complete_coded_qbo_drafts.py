"""
Complete pull-born dbo.Expense drafts that are fully coded in QBO but still draft locally (U-739).

Uses the same eligibility rules as the purchase pull UPDATE-path auto-complete. Dry-run by
default; --apply calls ExpenseService.complete_expense under system_authz per expense.

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/backfill_complete_coded_qbo_drafts.py \\
      --out /tmp/u739_preview.csv
  PYTHONPATH=. ./.venv/bin/python scripts/backfill_complete_coded_qbo_drafts.py \\
      --apply --limit 50 --out /tmp/u739_apply.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from types import SimpleNamespace
from typing import Any, Optional, Sequence

from entities.expense.business.service import ExpenseService
from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    expense_autocomplete_skip_reason,
)
from scripts.sync_helper import assert_cli_system_admin
from shared.authz.context import system_authz
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_complete_coded_qbo_drafts")

CANDIDATE_EXPENSES_SQL = """
SELECT
    e.[Id] AS ExpenseId,
    e.[PublicId] AS PublicId,
    p.[Id] AS QboPurchaseId,
    p.[QboId] AS QboPurchaseQboId
FROM dbo.[Expense] e
INNER JOIN qbo.[Purchase] p
    ON p.[QboId] = e.[QboId] AND p.[RealmId] = e.[RealmId]
WHERE e.[Status] = N'draft'
  AND e.[StatusOrigin] IN (N'qbo_pull', N'coding_backfill')
  AND e.[QboId] IS NOT NULL
  AND e.[RealmId] IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM qbo.[PurchaseLine] pl
      WHERE pl.[QboPurchaseId] = p.[Id]
        AND pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
        AND pl.[ItemRefValue] IS NULL
  )
  AND EXISTS (
      SELECT 1 FROM dbo.[ExpenseLineItem] eli WHERE eli.[ExpenseId] = e.[Id]
  )
  AND NOT EXISTS (
      SELECT 1
      FROM dbo.[ExpenseLineItem] eli
      LEFT JOIN dbo.[SubCostCode] scc ON scc.[Id] = eli.[SubCostCodeId]
      WHERE eli.[ExpenseId] = e.[Id]
        AND (
            eli.[SubCostCodeId] IS NULL
            OR ISNULL(scc.[IsHoldingCode], 0) = 1
            OR LTRIM(RTRIM(scc.[Number])) = N'00.02'
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM dbo.[ExpenseLineItem] eli
      WHERE eli.[ExpenseId] = e.[Id]
        AND (
            eli.[QboId] IS NULL
            OR NOT EXISTS (
                SELECT 1
                FROM qbo.[PurchaseLine] pl
                WHERE pl.[QboPurchaseId] = p.[Id]
                  AND pl.[QboLineId] = eli.[QboId]
            )
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM qbo.[PurchaseLine] pl
      WHERE pl.[QboPurchaseId] = p.[Id]
        AND NOT EXISTS (
            SELECT 1
            FROM dbo.[ExpenseLineItem] eli
            WHERE eli.[ExpenseId] = e.[Id]
              AND eli.[QboId] = pl.[QboLineId]
        )
  )
  AND (? IS NULL OR e.[Id] = ?)
ORDER BY e.[Id];
"""

STAGED_LINES_SQL = """
SELECT
    pl.[QboLineId],
    pl.[AccountRefName],
    pl.[ItemRefValue]
FROM qbo.[PurchaseLine] pl
WHERE pl.[QboPurchaseId] = ?;
"""

LOCAL_LINES_SQL = """
SELECT
    eli.[Id],
    eli.[QboId],
    eli.[SubCostCodeId]
FROM dbo.[ExpenseLineItem] eli
WHERE eli.[ExpenseId] = ?;
"""

EXPENSE_HEADER_SQL = """
SELECT [Id], [PublicId], [Status], [StatusOrigin]
FROM dbo.[Expense]
WHERE [Id] = ?;
"""

CSV_FIELDNAMES = [
    "expense_id",
    "public_id",
    "qbo_purchase_id",
    "line_count",
    "outcome",
]


def _staged_lines_from_rows(rows) -> list:
    return [
        SimpleNamespace(
            qbo_line_id=getattr(row, "QboLineId", None),
            account_ref_name=getattr(row, "AccountRefName", None),
            item_ref_value=getattr(row, "ItemRefValue", None),
        )
        for row in rows
    ]


def _local_lines_from_rows(rows) -> list:
    return [
        SimpleNamespace(
            id=getattr(row, "Id", None),
            qbo_id=getattr(row, "QboId", None),
            sub_cost_code_id=getattr(row, "SubCostCodeId", None),
        )
        for row in rows
    ]


def evaluate_expense_row(
    cursor,
    expense_id: int,
    public_id: str,
    qbo_purchase_id: int,
    *,
    apply: bool,
    expense_service: ExpenseService,
    sub_cost_code_service,
) -> tuple[str, int]:
    cursor.execute(EXPENSE_HEADER_SQL, expense_id)
    header = cursor.fetchone()
    if header is None:
        return "error:NotFound", 0

    cursor.execute(LOCAL_LINES_SQL, expense_id)
    local_rows = cursor.fetchall()
    line_count = len(local_rows)

    cursor.execute(STAGED_LINES_SQL, qbo_purchase_id)
    staged = _staged_lines_from_rows(cursor.fetchall())
    local_lines = _local_lines_from_rows(local_rows)

    expense = SimpleNamespace(
        id=expense_id,
        public_id=public_id,
        status=getattr(header, "Status", None),
        status_origin=getattr(header, "StatusOrigin", None),
    )
    skip = expense_autocomplete_skip_reason(
        expense, staged, local_lines, sub_cost_code_service
    )
    if skip:
        return f"skipped:{skip}", line_count

    if not apply:
        return "would_complete", line_count

    try:
        with system_authz():
            result = expense_service.complete_expense(str(public_id))
    except Exception as exc:
        return f"error:{exc.__class__.__name__}", line_count

    if not result.get("expense_finalized"):
        msg = str(result.get("message") or "complete_not_finalized")
        if len(msg) > 80:
            msg = msg[:80]
        return f"error:{msg}", line_count
    errors = result.get("errors") or []
    if errors:
        return f"completed_with_errors:{len(errors)}", line_count
    return "completed", line_count


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Run complete_expense (default dry-run)")
    parser.add_argument("--expense-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", required=True, help="CSV path for per-expense audit")
    args = parser.parse_args(argv)

    assert_cli_system_admin()

    from entities.sub_cost_code.business.service import SubCostCodeService

    expense_service = ExpenseService()
    scc_svc = SubCostCodeService()
    counts: dict[str, int] = {}

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(CANDIDATE_EXPENSES_SQL, args.expense_id, args.expense_id)
        candidates = cursor.fetchall()
        if args.limit is not None:
            candidates = candidates[: args.limit]

        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for row in candidates:
                expense_id = int(row.ExpenseId)
                outcome = "error:Unknown"
                line_count = 0
                try:
                    outcome, line_count = evaluate_expense_row(
                        cursor,
                        expense_id,
                        str(row.PublicId),
                        int(row.QboPurchaseId),
                        apply=args.apply,
                        expense_service=expense_service,
                        sub_cost_code_service=scc_svc,
                    )
                    # complete_expense opens its own DB connections; no txn to commit here.
                except Exception as exc:
                    outcome = f"error:{exc.__class__.__name__}"
                    logger.exception("expense %s failed", expense_id)
                counts[outcome] = counts.get(outcome, 0) + 1
                writer.writerow(
                    {
                        "expense_id": expense_id,
                        "public_id": str(row.PublicId),
                        "qbo_purchase_id": int(row.QboPurchaseId),
                        "line_count": line_count,
                        "outcome": outcome,
                    }
                )
                fh.flush()

    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
