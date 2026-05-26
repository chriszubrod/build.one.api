"""Regression check on Bill read sprocs projecting SourceEmailMessageId.

Surfaced 2026-05-19 manual backlog walk: `BillRepository.read_by_id(18545)`
returned `source_email_message_id=None` despite the underlying row carrying
`SourceEmailMessageId=752`. Root cause: 8 sproc projections in
`entities/bill/sql/dbo.bill.sql` (5 reads + Update OUTPUT + Delete OUTPUT +
paginated) all omitted the column. Fixed 2026-05-26 via
`entities/bill/sql/migrations/003_read_bill_source_email_message_id.sql`.

This script locks the contract:

  - At least one Bill in prod has a non-null SourceEmailMessageId; we
    pick one as the fixture and assert each read path returns it.
  - read_by_id, read_by_public_id, and read_by_bill_number_and_vendor
    all hydrate the FK consistently.

Update/Delete OUTPUT projections are not exercised — destructive operations
are out of scope for a read-only regression script. They share the same
column list as the Read sprocs; the migration applied them together.

Run:
    .venv/bin/python scripts/verify_bill_source_email_projection.py

Exits 0 on PASS, 1 on FAIL. Read-only — no mutations.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.authz.context import set_authz_context
from shared.database import get_connection


def _pick_fixture_bill() -> tuple[int, str, str, int]:
    """Return (bill_id, vendor_public_id, bill_number, source_email_message_id)
    for the most-recently-created Bill with a non-null SourceEmailMessageId.
    Bypasses the service layer because we need raw DB ground truth."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT TOP 1 b.[Id], CAST(v.[PublicId] AS NVARCHAR(36)),
                            b.[BillNumber], b.[SourceEmailMessageId]
               FROM dbo.[Bill] b
               INNER JOIN dbo.[Vendor] v ON v.[Id] = b.[VendorId]
               WHERE b.[SourceEmailMessageId] IS NOT NULL
               ORDER BY b.[Id] DESC"""
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                "No Bill in prod has a non-null SourceEmailMessageId — "
                "fixture cannot be selected. The agent flow must have "
                "regressed (no email-driven bills) or the table was reset."
            )
        return int(row[0]), str(row[1]), str(row[2]), int(row[3])


def verify() -> int:
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)

    # Lazy-import so the authz context is set before the service module
    # initializes anything access-gated.
    from entities.bill.business.service import BillService

    bill_id, vendor_public_id, bill_number, expected_sem_id = _pick_fixture_bill()
    print(f"=== Bill.SourceEmailMessageId projection check ===")
    print(f"  fixture bill_id          : {bill_id}")
    print(f"  fixture vendor_public_id : {vendor_public_id}")
    print(f"  fixture bill_number      : {bill_number!r}")
    print(f"  expected SEM id          : {expected_sem_id}")

    svc = BillService()
    failures: list[str] = []

    b_by_id = svc.read_by_id(bill_id)
    if b_by_id is None:
        failures.append(f"read_by_id({bill_id}) returned None")
    elif b_by_id.source_email_message_id != expected_sem_id:
        failures.append(
            f"read_by_id: expected source_email_message_id={expected_sem_id}, "
            f"got {b_by_id.source_email_message_id!r}"
        )

    if b_by_id is not None:
        b_by_pub = svc.read_by_public_id(str(b_by_id.public_id))
        if b_by_pub is None:
            failures.append(f"read_by_public_id({b_by_id.public_id}) returned None")
        elif b_by_pub.source_email_message_id != expected_sem_id:
            failures.append(
                f"read_by_public_id: expected source_email_message_id={expected_sem_id}, "
                f"got {b_by_pub.source_email_message_id!r}"
            )

    b_by_num = svc.read_by_bill_number_and_vendor_public_id(
        bill_number, vendor_public_id,
    )
    if b_by_num is None:
        failures.append(
            f"read_by_bill_number_and_vendor_public_id({bill_number!r}, "
            f"{vendor_public_id}) returned None"
        )
    elif b_by_num.source_email_message_id != expected_sem_id:
        failures.append(
            f"read_by_bill_number_and_vendor_public_id: expected "
            f"source_email_message_id={expected_sem_id}, "
            f"got {b_by_num.source_email_message_id!r}"
        )

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS — all 3 read paths return source_email_message_id correctly")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
