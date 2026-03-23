#!/usr/bin/env python3
"""
Find and remove duplicate BillLineItems caused by QBO pull sync.
For each (BillId, SubCostCodeId, Description, Amount) group with
more than one row, keeps the row with a BillLineItemBillLine mapping
(or the oldest Id on a tie) and deletes the rest.

Usage:
    python scripts/fix_duplicate_bill_line_items.py              # all projects
    python scripts/fix_duplicate_bill_line_items.py 94           # MR2-SITE
    python scripts/fix_duplicate_bill_line_items.py 128          # Haverford
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database import get_connection

project_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
project_filter = "AND bli.ProjectId = ?" if project_id else ""
params = [project_id] if project_id else []

FIND_DUPLICATES = f"""
SELECT
    p.Name              AS ProjectName,
    bli.ProjectId,
    bli.BillId,
    b.BillNumber,
    v.Name              AS VendorName,
    scc.Number          AS SubCostCode,
    LEFT(bli.Description, 60) AS Description,
    bli.Amount,
    bli.Id              AS BillLineItemId,
    CASE WHEN blbl.BillLineItemId IS NOT NULL THEN 1 ELSE 0 END AS HasMapping,
    ROW_NUMBER() OVER (
        PARTITION BY bli.BillId, bli.SubCostCodeId,
                     LEFT(bli.Description, 60), bli.Amount
        ORDER BY
            CASE WHEN blbl.BillLineItemId IS NOT NULL THEN 0 ELSE 1 END,
            bli.Id ASC
    ) AS rn
FROM dbo.BillLineItem bli
JOIN dbo.Bill b ON b.Id = bli.BillId
JOIN dbo.Vendor v ON v.Id = b.VendorId
LEFT JOIN dbo.Project p ON p.Id = bli.ProjectId
LEFT JOIN dbo.SubCostCode scc ON scc.Id = bli.SubCostCodeId
LEFT JOIN qbo.BillLineItemBillLine blbl ON blbl.BillLineItemId = bli.Id
WHERE bli.BillId IN (
    SELECT BillId FROM dbo.BillLineItem bli2
    WHERE 1=1 {project_filter}
    GROUP BY BillId, SubCostCodeId, LEFT(Description, 60), Amount
    HAVING COUNT(*) > 1
)
{project_filter}
ORDER BY p.Name, b.BillNumber, bli.SubCostCodeId, bli.Id
"""

with get_connection() as conn:
    cursor = conn.cursor()

    # --- Diagnostic ---
    cursor.execute(FIND_DUPLICATES, params + params)
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]

    if not rows:
        print("No duplicate BillLineItems found.")
        sys.exit(0)

    keepers = [r for r in rows if r[cols.index('rn')] == 1]
    dupes   = [r for r in rows if r[cols.index('rn')] > 1]

    print(f"\nFound {len(dupes)} duplicate(s) across {len(keepers)} group(s):\n")
    print(f"  {'ID':>8}  {'Keep?':6}  {'Mapped':6}  {'Project':<15}  {'Bill':<12}  {'Vendor':<25}  {'SCC':<8}  {'Amount':>10}  {'Description'}")
    print("  " + "-"*110)
    for r in rows:
        row = dict(zip(cols, r))
        keep = "KEEP" if row['rn'] == 1 else "DELETE"
        mapped = "YES" if row['HasMapping'] else "no"
        print(f"  {row['BillLineItemId']:>8}  {keep:<6}  {mapped:<6}  {str(row['ProjectName'] or ''):<15}  {str(row['BillNumber'] or ''):<12}  {str(row['VendorName'] or ''):<25}  {str(row['SubCostCode'] or ''):<8}  {float(row['Amount'] or 0):>10.2f}  {str(row['Description'] or '')}")

    delete_ids = [r[cols.index('BillLineItemId')] for r in dupes]
    print(f"\nWill delete {len(delete_ids)} BillLineItem(s): {delete_ids}")
    confirm = input("\nProceed with delete? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    # --- Delete InvoiceLineItem references ---
    placeholders = ",".join("?" * len(delete_ids))
    cursor.execute(f"""
        DELETE ili FROM dbo.InvoiceLineItem ili
        WHERE ili.BillLineItemId IN ({placeholders})
    """, delete_ids)
    ili_deleted = cursor.rowcount
    print(f"Removed {ili_deleted} InvoiceLineItem reference(s).")

    # --- Delete duplicate BillLineItems ---
    cursor.execute(f"""
        DELETE FROM dbo.BillLineItem WHERE Id IN ({placeholders})
    """, delete_ids)
    bli_deleted = cursor.rowcount
    print(f"Deleted {bli_deleted} duplicate BillLineItem(s).")

    conn.commit()
    print("\nDone.")
