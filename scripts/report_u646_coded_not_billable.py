"""U-646 read-only report: coded expense lines marked Not Billable (IsBillable = 0).

Prints CSV to stdout for Chris's review (U-640 decision: report, not UPDATE).
No writes — single SELECT only.

Run from repo root:
    ./.venv/bin/python scripts/report_u646_coded_not_billable.py
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection

_QUERY = """
SELECT
    eli.[PublicId],
    CONVERT(VARCHAR(19), e.[ExpenseDate], 120) AS [ExpenseDate],
    v.[Name] AS [VendorName],
    p.[Name] AS [ProjectName],
    p.[IsCostPlus],
    eli.[Description],
    eli.[Amount]
FROM dbo.[ExpenseLineItem] eli
INNER JOIN dbo.[Expense] e ON e.[Id] = eli.[ExpenseId]
INNER JOIN dbo.[Vendor] v ON v.[Id] = e.[VendorId]
INNER JOIN dbo.[Project] p ON p.[Id] = eli.[ProjectId]
WHERE eli.[ProjectId] IS NOT NULL
  AND eli.[IsBillable] = 0
ORDER BY e.[ExpenseDate] DESC
"""


def main() -> int:
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "line_public_id",
            "expense_date",
            "vendor_name",
            "project_name",
            "project_is_cost_plus",
            "description",
            "amount",
        ]
    )
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_QUERY)
        for row in cursor.fetchall():
            writer.writerow(
                [
                    str(row.PublicId),
                    row.ExpenseDate,
                    row.VendorName,
                    row.ProjectName,
                    "" if row.IsCostPlus is None else int(row.IsCostPlus),
                    row.Description or "",
                    row.Amount,
                ]
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
