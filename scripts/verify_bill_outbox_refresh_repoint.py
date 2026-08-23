"""Equivalence + fast-path check for the Bill outbox refresh's U-301b repoint.

integrations/intuit/qbo/outbox/business/worker.py::_refresh_bill used to
resolve which QBO Bill to re-pull (on a SyncToken-mismatch retry) purely via
the qbo.BillBill -> qbo.Bill two-hop. U-301b tries dbo.Bill's own native
QboId (U-238a) first via the shared verify_bill_qbo_identity wrapper
(base/identity_consistency.py), falling back to the legacy two-hop when dbo
has no answer yet, and hard-refusing (recording a bill_identity_conflict
ReconciliationIssue) when the two sides genuinely disagree.

Two checks, both against live prod, read-only:

1. Population equivalence -- for every dbo.Bill with QboId set AND a
   BillBill mapping, dbo.Bill.QboId MUST equal the mapping's resolved
   qbo.Bill.QboId. A disagreement here is exactly the hard-refuse trigger;
   this script surfaces it as a FAIL rather than silently exercising it in
   prod for the first time.
2. Full-chain fast-path proof -- since this unit also added [QboId]/[RealmId]
   to ReadBillByPublicId (not yet deployed), a temp-named copy of the real
   sproc body (read live from the .sql file) is executed against a real
   Bill row to prove the whole chain (sproc -> Bill._from_db -> bill.qbo_id)
   surfaces the identity correctly once deployed.

Run:
    .venv/bin/python scripts/verify_bill_outbox_refresh_repoint.py

Exits 0 on PASS, 1 on FAIL. Read-only -- no mutations (check 2 runs inside a
transaction that is always rolled back).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection

SQL_FILE = Path(__file__).resolve().parent.parent / "entities" / "bill" / "sql" / "dbo.bill.sql"
SENTINEL = "U301B_VERIFY_ROLLBACK"


def _check_population_equivalence(cur) -> list:
    failures = []

    cur.execute(
        """
        SELECT b.Id, b.QboId AS DboQboId, qb.QboId AS LegacyQboId
        FROM dbo.Bill b
        JOIN qbo.BillBill map ON map.BillId = b.Id
        JOIN qbo.Bill qb ON qb.Id = map.QboBillId
        WHERE b.QboId IS NOT NULL
        """
    )
    rows = cur.fetchall()
    disagree = [r for r in rows if r.DboQboId != r.LegacyQboId]
    print(f"Bills with dbo.QboId set AND a BillBill mapping: {len(rows)}, disagreements: {len(disagree)}")
    if disagree:
        sample = [(r.Id, r.DboQboId, r.LegacyQboId) for r in disagree[:10]]
        failures.append(
            f"{len(disagree)} Bill(s) have a dbo.Bill.QboId that disagrees with their own "
            f"BillBill mapping's resolved external QboId -- these will hit the hard-refuse "
            f"path on their next outbox refresh. Sample (Id, dbo_qbo_id, legacy_qbo_id): {sample}"
        )

    cur.execute(
        """
        SELECT COUNT(*) FROM dbo.Bill b
        WHERE b.QboId IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM qbo.BillBill map WHERE map.BillId = b.Id)
        """
    )
    unmapped_with_identity = cur.fetchone()[0]
    print(f"dbo.Bill with QboId set but no BillBill mapping (fast path trusts, nothing to disagree with): {unmapped_with_identity}")

    return failures


def _check_fast_path_full_chain(cur) -> list:
    from entities.bill.persistence.repo import BillRepository

    failures = []

    text = SQL_FILE.read_text()
    match = re.search(
        r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+ReadBillByPublicId\b.*?(?=\nGO)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ["could not locate ReadBillByPublicId in dbo.bill.sql"]
    temp_sql = match.group(0).replace(
        "CREATE OR ALTER PROCEDURE ReadBillByPublicId",
        "CREATE OR ALTER PROCEDURE ReadBillByPublicId_U301b_Verify",
        1,
    )
    if "[QboId]" not in temp_sql or "[RealmId]" not in temp_sql:
        return ["ReadBillByPublicId's SQL no longer selects QboId/RealmId -- U-301b's fast path would go dark"]

    cur.execute("SELECT TOP 1 PublicId, QboId, RealmId FROM dbo.Bill WHERE QboId IS NOT NULL ORDER BY Id")
    row = cur.fetchone()
    if not row:
        print("No dbo.Bill row with QboId set exists -- skipping full-chain check (nothing to test against).")
        return failures

    cur.execute(temp_sql)
    cur.execute("EXEC dbo.ReadBillByPublicId_U301b_Verify @PublicId=?", row.PublicId)
    result_row = cur.fetchone()

    bill = BillRepository()._from_db(result_row)
    print(f"Full-chain check: Bill.qbo_id={bill.qbo_id!r} realm_id={bill.realm_id!r}")
    if bill.qbo_id != row.QboId or bill.realm_id != row.RealmId:
        failures.append(
            f"Full-chain mismatch: expected qbo_id={row.QboId!r}/realm_id={row.RealmId!r}, "
            f"got qbo_id={bill.qbo_id!r}/realm_id={bill.realm_id!r}"
        )
    return failures


def verify() -> int:
    all_failures = []
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            all_failures.extend(_check_population_equivalence(cur))
            all_failures.extend(_check_fast_path_full_chain(cur))
            raise RuntimeError(SENTINEL)  # always roll back -- nothing persists
    except RuntimeError as e:
        if SENTINEL not in str(e):
            raise

    if all_failures:
        print("\nFAIL:")
        for f in all_failures:
            print(f"  - {f}")
        return 1

    print("\nPASS -- old and new Bill identity resolution agree; fast path verified end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
