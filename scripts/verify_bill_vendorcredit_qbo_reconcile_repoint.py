"""Equivalence check for the Bill + VendorCredit reconciliation detectors'
U-305 repoint.

integrations/intuit/qbo/reconciliation/business/service.py's
_reconcile_{bill,vendor_credit}_qbo_{missing_locally,voided} used to resolve
"is this QBO record already synced locally" via a per-record
qbo.{Bill,VendorCredit} + qbo.{BillBill,VendorCreditBillCredit}
staging/mapping round trip. U-305 repointed both onto dbo.{Bill,BillCredit}'s
own native QboId (U-238a/U-238c), loaded once per run via
identity_drift.py's registry-driven read_qbo_identity_rows_by_realm_id
(Decision-1 — one generic reader, not two hand-copied entity-specific
sprocs).

Mirrors U-301a's verify_expense_qbo_reconcile_repoint.py: the durable check is
a direct population-equivalence query per family — the set of QboIds the OLD
mapping-table-driven logic would have treated as "fully synced" MUST exactly
match dbo.{Bill,BillCredit}.QboId for the same realm. A non-empty `only_old`
diff is exactly the P2 risk a code review raised against this repoint (a
qbo.*-mapped-but-not-yet-dbo-stamped "pending_backfill" row would be invisible
to the new bulk reader) — this script is the proof that risk isn't live in
today's data, the same acceptance bar U-301a's Expense pilot used
(11557/11557, 0 divergence).

Run:
    .venv/bin/python scripts/verify_bill_vendorcredit_qbo_reconcile_repoint.py

Exits 0 on PASS, 1 on FAIL. Read-only -- no mutations.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection


# U-305 investigation (2026-08-23): QBO Bill id 65042 (qbo.Bill.Id=17204,
# realm 9130353016965726) maps via qbo.BillBill.Id=16790 to BillId=16808 -- a
# dbo.Bill.Id that does not exist. This is a PRE-EXISTING dangling
# qbo.BillBill mapping row, unrelated to this repoint (the OLD reconciliation
# logic already silently treated it as "fully synced" -- a mapping-repo
# lookup returning a row is truthy regardless of whether the mapped id
# resolves -- so this was never actually checked by the pre-repoint detector
# either). Not fixable by scripts/backfill_qbo_identity_headers.py (there is
# no dbo.Bill row to stamp). Booked as a DBA cleanup item, not blocking this
# repoint.
#
# Allowlisted here, keyed by (label, realm_id, qbo_id) -- NOT bare qbo_id --
# so a different realm's real pending_backfill row can never be masked just
# because it happens to reuse the same QboId string (QboIds are only unique
# per-realm). _verify_family also re-confirms the excused row's target dbo.Bill
# row is still genuinely absent before excusing it each run -- if a future
# fix ever creates dbo.Bill.Id=16808, the exception stops applying and this
# script correctly starts failing again instead of silently staying green.
KNOWN_ORPHANED_MAPPINGS = {
    ("Bill", "9130353016965726", "65042"): {"target_dbo_id": 16808, "dbo_table": "dbo.Bill"},
}


# U-353: the "BillCredit" family entry was removed — qbo.VendorCreditBillCredit
# (its mapping_table) is retired, so this script's mapping-table JOIN would raise
# "Invalid object name" once /em applies the DROP. The U-305 equivalence proof
# this script exists for was already completed for BillCredit (see BOARD.md);
# "Bill" (still on its own mapping table, family 6, not yet retired) stays live.
FAMILIES = [
    {
        "label": "Bill",
        "staging_table": "qbo.Bill",
        "mapping_table": "qbo.BillBill",
        "mapping_fk_col": "QboBillId",
        "dbo_table": "dbo.Bill",
    },
]


def _excused_qbo_ids(cur, label: str, realm_id: str) -> set[str]:
    """Known orphaned-mapping QboIds for this (label, realm_id), re-verified
    live each run: only excused if the exception's target dbo row is STILL
    genuinely absent (see KNOWN_ORPHANED_MAPPINGS' comment)."""
    excused = set()
    for (known_label, known_realm, qbo_id), exc in KNOWN_ORPHANED_MAPPINGS.items():
        if known_label != label or known_realm != realm_id:
            continue
        cur.execute(f"SELECT 1 FROM {exc['dbo_table']} WHERE Id = ?", exc["target_dbo_id"])
        if cur.fetchone() is None:
            excused.add(qbo_id)
    return excused


def _verify_family(cur, family: dict) -> list[str]:
    failures = []
    label = family["label"]

    cur.execute(
        f"""
        SELECT RealmId FROM {family['dbo_table']} WHERE QboId IS NOT NULL
        UNION
        SELECT RealmId FROM {family['staging_table']} WHERE RealmId IS NOT NULL
        """
    )
    realms = [r[0] for r in cur.fetchall()]
    print(f"\n=== {label} — realms to compare: {realms} ===")

    for realm_id in realms:
        cur.execute(
            f"""
            SELECT qs.QboId
            FROM {family['staging_table']} qs
            JOIN {family['mapping_table']} map ON map.{family['mapping_fk_col']} = qs.Id
            WHERE qs.RealmId = ?
            """,
            realm_id,
        )
        old_mapped = {r[0] for r in cur.fetchall()}

        cur.execute(
            f"SELECT QboId FROM {family['dbo_table']} WHERE RealmId = ? AND QboId IS NOT NULL",
            realm_id,
        )
        new_mapped = {r[0] for r in cur.fetchall()}

        known_orphaned = _excused_qbo_ids(cur, label, realm_id)
        only_old = old_mapped - new_mapped - known_orphaned
        excused = (old_mapped - new_mapped) & known_orphaned
        only_new = new_mapped - old_mapped
        print(
            f"  realm={realm_id}: OLD={len(old_mapped)} NEW={len(new_mapped)} "
            f"only_old={len(only_old)} only_new={len(only_new)}"
            + (f" (excused: {sorted(excused)})" if excused else "")
        )
        if only_old:
            failures.append(
                f"{label} realm {realm_id}: {len(only_old)} QboId(s) mapped under the OLD "
                f"{family['staging_table']}+{family['mapping_table']} logic but missing from "
                f"{family['dbo_table']}.QboId -- likely a pending_backfill row (mapped in qbo.* "
                f"staging, not yet dbo-stamped) -- the repoint would newly report these as "
                f"'missing locally' and the voided detector would never see them. "
                f"Sample: {sorted(only_old)[:10]}"
            )
        if only_new:
            failures.append(
                f"{label} realm {realm_id}: {len(only_new)} QboId(s) stamped on {family['dbo_table']} "
                f"but not reflected in the OLD {family['staging_table']}+{family['mapping_table']} "
                f"mapping -- the repoint would newly treat these as synced when the old logic did not. "
                f"Sample: {sorted(only_new)[:10]}"
            )

    return failures


def verify() -> int:
    all_failures = []
    with get_connection() as conn:
        cur = conn.cursor()
        for family in FAMILIES:
            all_failures.extend(_verify_family(cur, family))

    if all_failures:
        print("\nFAIL:")
        for f in all_failures:
            print(f"  - {f}")
        return 1

    checked = ", ".join(f["label"] for f in FAMILIES)
    print(f"\nPASS -- old and new identity-resolution populations are identical for {checked}.")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
