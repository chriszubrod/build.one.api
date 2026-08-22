"""Regression check on ProposeInvoiceSourceLinks Tier 0c/0d (U-301c, 2026-08-22).

Tier 0c/0d resolve an invoice line's Bill/Purchase-linked source via
LinkedTxnId (exact header identity) + an amount fingerprint against the
identified header's own dbo line items -- repointed off qbo.Bill/qbo.BillLine/
qbo.BillLineItemBillLine and qbo.Purchase/qbo.PurchaseLine/
qbo.PurchaseLineExpenseLineItem staging by U-301c, onto dbo.Bill/dbo.Expense's
native QboId/RealmId (U-238a).

Zero live InvoiceLineItemSourceProvenance rows carry LinkedTxnType IN
('Bill', 'Purchase') as of 2026-08-22, so there is no live-traffic canary for
this join shape, and tests/conftest.py's post-U-295 guard blocks real DB
connections from the pytest suite. This script is the durable substitute --
run manually pre-deploy or whenever Tier 0c/0d changes.

It runs a temp-named copy of the CURRENT sproc body (read live from
entities/invoice/sql/dbo.invoice.sql, so this check can never silently drift
from what actually ships) against synthetic rows, all inside one transaction
that is ALWAYS rolled back -- nothing persists, safe to re-run against prod
repeatedly.

The `unmapped_sibling_precision_gate` scenario encodes the exact live-data
regression found + fixed the same day this sproc was first repointed --
full forensic detail (Bill.Id, QboId, sibling line Ids) is in
docs/staging_removal_phase4_5_scoping.md's "U-301c fix" note rather than
repeated here.

Run:
    .venv/bin/python scripts/verify_propose_invoice_source_links_tier0.py

Exits 0 on PASS, 1 on FAIL. Uses a real transaction rolled back at the end --
no mutations persist, but requires DB write privileges (not read-only).
"""
import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection

SQL_FILE = Path(__file__).resolve().parent.parent / "entities" / "invoice" / "sql" / "dbo.invoice.sql"
SPROC_NAME = "ProposeInvoiceSourceLinks"
TEMP_SPROC_NAME = "ProposeInvoiceSourceLinks_U301c_Verify"

# Staging tables Tier 0c/0d must never read again — an accidental revert
# (merge conflict, "cleanup", a regenerated base file) is caught by
# _extract_temp_sproc_sql()'s assertion below on every run, and permanently by
# tests/test_propose_invoice_source_links_no_staging.py (imports both this
# tuple and extract_sproc_body rather than re-deriving them) on every pytest
# run — no separate DB-level "staging populated, no dbo identity" scenario is
# needed here, that outcome is already guaranteed by this text check.
BANNED_STAGING_REFS = (
    "qbo.[Bill]", "qbo.[BillLine]", "qbo.[BillLineItemBillLine]",
    "qbo.[Purchase]", "qbo.[PurchaseLine]", "qbo.[PurchaseLineExpenseLineItem]",
)

REALM = "U301C-VERIFY-REALM"
SENTINEL = "U301C_VERIFY_ROLLBACK_SENTINEL"

# Anchored on the `\nGO` batch boundary via lookahead (not on a literal `END;`
# immediately before it) — sturdier against a future nested BEGIN...END block
# or a trailing comment, same technique tests/test_sproc_single_source.py uses
# for its own (more generic, multi-sproc) body-extraction pattern.
_SPROC_BODY_PATTERN = re.compile(
    rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+{SPROC_NAME}\b.*?(?=\nGO)",
    re.IGNORECASE | re.DOTALL,
)


@lru_cache(maxsize=1)
def extract_sproc_body() -> str:
    """The current ProposeInvoiceSourceLinks CREATE OR ALTER block, verbatim."""
    text = SQL_FILE.read_text()
    match = _SPROC_BODY_PATTERN.search(text)
    if not match:
        raise AssertionError(f"could not locate {SPROC_NAME} in {SQL_FILE}")
    return match.group(0)


def _extract_temp_sproc_sql() -> str:
    temp_sql = extract_sproc_body().replace(
        f"CREATE OR ALTER PROCEDURE {SPROC_NAME}",
        f"CREATE OR ALTER PROCEDURE {TEMP_SPROC_NAME}",
        1,
    )
    for banned in BANNED_STAGING_REFS:
        if banned in temp_sql:
            raise AssertionError(f"found banned staging reference: {banned}")
    return temp_sql


def verify() -> int:
    temp_sql = _extract_temp_sproc_sql()
    results = {}

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            cur.execute("SELECT TOP 1 Id FROM dbo.Project ORDER BY Id")
            project_id = cur.fetchone()[0]
            cur.execute("SELECT TOP 1 Id FROM dbo.Vendor ORDER BY Id")
            vendor_id = cur.fetchone()[0]

            cur.execute(temp_sql)

            def new_invoice() -> int:
                cur.execute(
                    """
                    INSERT INTO dbo.Invoice
                        (ProjectId, InvoiceDate, DueDate, InvoiceNumber, RealmId, IsDraft, CreatedDatetime)
                    OUTPUT INSERTED.Id
                    VALUES (?, SYSDATETIME(), SYSDATETIME(), 'U301C-VERIFY-INV', ?, 1, SYSDATETIME())
                    """,
                    project_id, REALM,
                )
                return cur.fetchone()[0]

            def new_line(invoice_id: int, amount, linked_txn_type: str, linked_txn_id: str) -> int:
                cur.execute(
                    """
                    INSERT INTO dbo.InvoiceLineItem (InvoiceId, SourceType, IsDraft, CreatedDatetime)
                    OUTPUT INSERTED.Id
                    VALUES (?, 'Manual', 1, SYSDATETIME())
                    """,
                    invoice_id,
                )
                ili_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO dbo.InvoiceLineItemSourceProvenance
                        (InvoiceLineItemId, QboAmount, LinkedTxnType, LinkedTxnId, CreatedDatetime)
                    VALUES (?, ?, ?, ?, SYSDATETIME())
                    """,
                    ili_id, amount, linked_txn_type, linked_txn_id,
                )
                return ili_id

            def new_bill(qbo_id: str, realm_id: str = REALM) -> int:
                cur.execute(
                    """
                    INSERT INTO dbo.Bill (VendorId, BillDate, DueDate, QboId, RealmId, IsDraft, CreatedDatetime)
                    OUTPUT INSERTED.Id
                    VALUES (?, SYSDATETIME(), SYSDATETIME(), ?, ?, 0, SYSDATETIME())
                    """,
                    vendor_id, qbo_id, realm_id,
                )
                return cur.fetchone()[0]

            def new_bill_line_item(bill_id: int, amount, qbo_id=None) -> int:
                cur.execute(
                    """
                    INSERT INTO dbo.BillLineItem (BillId, Amount, QboId, IsDraft, CreatedDatetime)
                    OUTPUT INSERTED.Id
                    VALUES (?, ?, ?, 0, SYSDATETIME())
                    """,
                    bill_id, amount, qbo_id,
                )
                return cur.fetchone()[0]

            def new_expense(qbo_id: str, realm_id: str = REALM) -> int:
                cur.execute(
                    """
                    INSERT INTO dbo.Expense
                        (VendorId, ExpenseDate, ReferenceNumber, QboId, RealmId, IsDraft, CreatedDatetime)
                    OUTPUT INSERTED.Id
                    VALUES (?, SYSDATETIME(), 'U301C-VERIFY-EXP-REF', ?, ?, 0, SYSDATETIME())
                    """,
                    vendor_id, qbo_id, realm_id,
                )
                return cur.fetchone()[0]

            def new_expense_line_item(expense_id: int, amount, qbo_id=None) -> int:
                cur.execute(
                    """
                    INSERT INTO dbo.ExpenseLineItem (ExpenseId, Amount, QboId, IsDraft, CreatedDatetime)
                    OUTPUT INSERTED.Id
                    VALUES (?, ?, ?, 0, SYSDATETIME())
                    """,
                    expense_id, amount, qbo_id,
                )
                return cur.fetchone()[0]

            def propose(invoice_id: int) -> dict:
                cur.execute(f"EXEC dbo.{TEMP_SPROC_NAME} @InvoiceId = ?", invoice_id)
                cols = [c[0] for c in cur.description]
                by_ili = {}
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    by_ili.setdefault(d["InvoiceLineItemId"], []).append(d)
                return by_ili

            invoice_id = new_invoice()

            # --- Scenario 1: Tier 0c single match ---
            bill1 = new_bill("U301C-BILL-1")
            bli1 = new_bill_line_item(bill1, 123.45, qbo_id="U301C-BILL-1-LINE-1")
            ili1 = new_line(invoice_id, 123.45, "Bill", "U301C-BILL-1")

            # --- Scenario 2: Tier 0d single match ---
            exp1 = new_expense("U301C-EXP-1")
            eli1 = new_expense_line_item(exp1, 67.89, qbo_id="U301C-EXP-1-LINE-1")
            ili2 = new_line(invoice_id, 67.89, "Purchase", "U301C-EXP-1")

            # --- Scenario 3: unmapped-sibling precision gate (live Bill.Id=16897 shape) ---
            bill2 = new_bill("U301C-BILL-2")
            bli2_mapped = new_bill_line_item(bill2, 999.00, qbo_id="U301C-BILL-2-LINE-1")
            bli2_unmapped = new_bill_line_item(bill2, 999.00, qbo_id=None)  # never individually synced
            ili3 = new_line(invoice_id, 999.00, "Bill", "U301C-BILL-2")

            # --- Scenario 4: RealmId mismatch -> zero candidates ---
            bill3 = new_bill("U301C-BILL-3", realm_id="U301C-OTHER-REALM")
            new_bill_line_item(bill3, 42.00, qbo_id="U301C-BILL-3-LINE-1")
            ili4 = new_line(invoice_id, 42.00, "Bill", "U301C-BILL-3")

            by_ili = propose(invoice_id)
            results["tier0c_single_match"] = (by_ili.get(ili1, []), bli1)
            results["tier0d_single_match"] = (by_ili.get(ili2, []), eli1)
            results["unmapped_sibling_precision_gate"] = (by_ili.get(ili3, []), bli2_mapped, bli2_unmapped)
            results["realm_mismatch"] = by_ili.get(ili4, [])

            raise RuntimeError(SENTINEL)  # force rollback no matter what
    except RuntimeError as e:
        if SENTINEL not in str(e):
            raise

    failures = []

    rows, expected_id = results["tier0c_single_match"]
    if not (len(rows) == 1 and rows[0]["SourceType"] == "BillLineItem"
            and rows[0]["SourceLineItemId"] == expected_id
            and rows[0]["Tier"] == 0 and rows[0]["DirectDbo"] is False
            and rows[0]["SourceLineNum"] is None):
        failures.append(f"tier0c_single_match: expected 1 clean BillLineItem candidate={expected_id}, got {rows}")

    rows, expected_id = results["tier0d_single_match"]
    if not (len(rows) == 1 and rows[0]["SourceType"] == "ExpenseLineItem"
            and rows[0]["SourceLineItemId"] == expected_id
            and rows[0]["Tier"] == 0 and rows[0]["DirectDbo"] is False
            and rows[0]["SourceLineNum"] is None):
        failures.append(f"tier0d_single_match: expected 1 clean ExpenseLineItem candidate={expected_id}, got {rows}")

    rows, mapped_id, unmapped_id = results["unmapped_sibling_precision_gate"]
    if not (len(rows) == 1 and rows[0]["SourceLineItemId"] == mapped_id):
        failures.append(
            f"unmapped_sibling_precision_gate: expected exactly the individually-mapped line "
            f"({mapped_id}), not its unmapped sibling ({unmapped_id}) — got {rows}"
        )

    rows = results["realm_mismatch"]
    if len(rows) != 0:
        failures.append(f"realm_mismatch: expected 0 candidates across realms, got {rows}")

    print("=== ProposeInvoiceSourceLinks Tier 0c/0d contract check ===")
    for key, val in results.items():
        print(f"  {key}: {val}")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS — transaction rolled back, no data persisted.")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
