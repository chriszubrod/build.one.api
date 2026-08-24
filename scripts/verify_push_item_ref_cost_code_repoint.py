"""Equivalence check for U-307b's push-side item-ref repoint.

The 3 QBO push connectors' `_get_qbo_item_ref`/`_get_qbo_item_ref_for_line`
(bill/purchase/invoice) used to resolve a local SubCostCode's outbound QBO
ItemRef by hopping `qbo.ItemSubCostCode -> qbo.Item.QboId`. U-307b repoints
all 3 onto `cost_code_resolver.resolve_qbo_item_ref`, which reads
`dbo.SubCostCode.QboId`/`.RealmId` directly -- no `qbo.Item` hop, and no
legacy-hop fallback on a miss (unlike this program's forward resolvers).

This script proves, for every SubCostCode the OLD legacy hop could resolve,
that the NEW dbo-native path resolves to the exact same QBO Item id, in the
exact same realm -- 0 divergence expected (U-289: 100% live parity). It also
explicitly counts (not just fails on) SubCostCodes the OLD hop could resolve
that the NEW path CANNOT (no dbo QboId stamped, or a realm mismatch) --
under the new no-fallback design these become newly-unresolvable pushes
instead of silently degrading through the legacy hop, so this count is the
real-world blast radius of that design point if it is ever nonzero.

Run:
    .venv/bin/python scripts/verify_push_item_ref_cost_code_repoint.py

Exits 0 on PASS (0 divergence AND 0 newly-unresolvable), 1 otherwise.
Read-only -- no mutations.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection


def verify() -> int:
    failures = []

    with get_connection() as conn:
        cur = conn.cursor()

        # OLD (legacy qbo.ItemSubCostCode -> qbo.Item hop) joined against NEW
        # (dbo-native SubCostCode identity) in one round trip -- every SubCostCode
        # the legacy hop can resolve, alongside what the new dbo-native path
        # resolves to for that same row.
        cur.execute(
            """
            SELECT isc.[SubCostCodeId], qi.[QboId], qi.[RealmId], scc.[QboId], scc.[RealmId]
            FROM [qbo].[ItemSubCostCode] isc
            JOIN [qbo].[Item] qi ON qi.[Id] = isc.[QboItemId]
            JOIN [dbo].[SubCostCode] scc ON scc.[Id] = isc.[SubCostCodeId]
            WHERE qi.[QboId] IS NOT NULL
            """
        )
        rows = cur.fetchall()
        print(f"OLD (legacy hop) resolvable SubCostCodes: {len(rows)}")

        divergent = []
        unresolvable_under_new = []

        for sub_cost_code_id, old_qbo_id, old_realm_id, new_qbo_id, new_realm_id in rows:
            if new_qbo_id is None:
                unresolvable_under_new.append(
                    f"SubCostCodeId={sub_cost_code_id}: OLD resolves to QboId={old_qbo_id!r} "
                    f"(realm={old_realm_id!r}), but dbo.SubCostCode.QboId is NULL -- the NEW "
                    f"no-fallback resolver returns None for this SubCostCode."
                )
                continue

            if new_qbo_id != old_qbo_id or new_realm_id != old_realm_id:
                divergent.append(
                    f"SubCostCodeId={sub_cost_code_id}: OLD=(QboId={old_qbo_id!r}, "
                    f"realm={old_realm_id!r}) NEW=(QboId={new_qbo_id!r}, realm={new_realm_id!r})"
                )

        print(f"Divergent (different QboId/realm between OLD and NEW): {len(divergent)}")
        for line in divergent:
            print(f"  DIVERGENT: {line}")
        print(f"Unresolvable under NEW (no dbo.SubCostCode.QboId stamped): {len(unresolvable_under_new)}")
        for line in unresolvable_under_new:
            print(f"  UNRESOLVABLE: {line}")

        if divergent:
            failures.append(
                f"{len(divergent)} SubCostCode(s) resolve to a DIFFERENT QBO Item/realm under "
                f"the new dbo-native path than the old legacy hop -- would misroute a live push."
            )
        if unresolvable_under_new:
            failures.append(
                f"{len(unresolvable_under_new)} SubCostCode(s) the old legacy hop could resolve "
                f"are unresolvable under the new no-fallback design -- these pushes would newly "
                f"fail with 'no QBO Item mapping' instead of degrading through the legacy hop. "
                f"Backfill dbo.SubCostCode.QboId for these before/alongside deploying U-307b, or "
                f"accept the regression explicitly."
            )

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS: 0 divergence, 0 newly-unresolvable SubCostCodes.")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
