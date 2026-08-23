"""Equivalence check for the Expense reconciliation detectors' U-301a repoint.

integrations/intuit/qbo/reconciliation/business/service.py's
_reconcile_purchase_qbo_missing_locally / _reconcile_purchase_qbo_voided used
to resolve "is this QBO Purchase already synced locally" via a per-record
qbo.Purchase + qbo.PurchaseExpense staging/mapping round trip. U-301a
repointed both onto dbo.Expense's own native QboId (U-238a), loaded once per
run via ExpenseService.read_qbo_ids_by_realm_id /
read_qbo_identity_rows_by_realm_id.

Unlike U-301c's ProposeInvoiceSourceLinks repoint (zero live rows to compare
against, forcing synthetic fixtures), this repoint has ~11.5k real rows today
-- so the durable check here is a direct population-equivalence query: the
set of QboIds the OLD mapping-table-driven logic would have treated as
"fully synced" MUST exactly match dbo.Expense.QboId for the same realm. A
future drift between the write-side dual-write (qbo.PurchaseExpense) and the
read-side dbo-native stamp (SetExpenseQboIdentity) would show up here as a
non-empty diff on either side.

Run:
    .venv/bin/python scripts/verify_expense_qbo_reconcile_repoint.py

Exits 0 on PASS, 1 on FAIL. Read-only -- no mutations.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection


def verify() -> int:
    failures = []

    with get_connection() as conn:
        cur = conn.cursor()

        # Union both sides -- a realm with OLD-side mapped Purchases but ZERO
        # dbo.Expense.QboId rows (e.g. the dual-write never fired for it) must
        # still surface as a divergence, not silently drop out of the loop.
        cur.execute(
            """
            SELECT RealmId FROM dbo.Expense WHERE QboId IS NOT NULL
            UNION
            SELECT RealmId FROM qbo.Purchase WHERE RealmId IS NOT NULL
            """
        )
        realms = [r[0] for r in cur.fetchall()]
        print(f"Realms to compare (union of dbo.Expense + qbo.Purchase): {realms}")

        for realm_id in realms:
            cur.execute(
                """
                SELECT qp.QboId
                FROM qbo.Purchase qp
                JOIN qbo.PurchaseExpense map ON map.QboPurchaseId = qp.Id
                WHERE qp.RealmId = ?
                """,
                realm_id,
            )
            old_mapped = {r[0] for r in cur.fetchall()}

            cur.execute(
                "SELECT QboId FROM dbo.Expense WHERE RealmId = ? AND QboId IS NOT NULL",
                realm_id,
            )
            new_mapped = {r[0] for r in cur.fetchall()}

            only_old = old_mapped - new_mapped
            only_new = new_mapped - old_mapped
            print(
                f"  realm={realm_id}: OLD={len(old_mapped)} NEW={len(new_mapped)} "
                f"only_old={len(only_old)} only_new={len(only_new)}"
            )
            if only_old:
                failures.append(
                    f"realm {realm_id}: {len(only_old)} QboId(s) mapped under the OLD "
                    f"qbo.Purchase+qbo.PurchaseExpense logic but missing from dbo.Expense.QboId "
                    f"-- the repoint would newly report these as 'missing locally'. "
                    f"Sample: {sorted(only_old)[:10]}"
                )
            if only_new:
                failures.append(
                    f"realm {realm_id}: {len(only_new)} QboId(s) stamped on dbo.Expense but not "
                    f"reflected in the OLD qbo.Purchase+qbo.PurchaseExpense mapping -- the repoint "
                    f"would newly treat these as synced when the old logic did not. "
                    f"Sample: {sorted(only_new)[:10]}"
                )

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS -- old and new identity-resolution populations are identical.")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
