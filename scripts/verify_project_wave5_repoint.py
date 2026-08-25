"""Live equivalence check for U-311's Project-family Wave-5 repoint.

Compares, for every live qbo.CustomerProject mapping row, the OLD
mapping-table-resolved Project against the NEW dbo-only direct resolution
(dbo.Project.QboId/RealmId matching the QboCustomer's own qbo_id/realm_id).
Re-verifies wave5.md's own §1 premise fresh (not from memory) immediately
before this unit ships, per the standing discipline.

Read-only. Exits 0 on 0 divergence / 0 orphans either direction, 1 otherwise.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection


def verify() -> int:
    failures = []
    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM [dbo].[Project]")
        total_projects = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM [dbo].[Project] WHERE [QboId] IS NOT NULL")
        stamped_projects = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM [qbo].[CustomerProject]")
        mapping_rows = cur.fetchone()[0]
        print(f"dbo.Project: {total_projects} total / {stamped_projects} QboId-stamped")
        print(f"qbo.CustomerProject mapping rows: {mapping_rows}")

        # Mapping -> dbo.Project QboId/RealmId disagreement (the OLD identity
        # source vs the NEW one U-311 now trusts alone for the 6 repointed
        # consumers + this connector's own pull).
        cur.execute(
            """
            SELECT cp.[QboCustomerId], cp.[ProjectId], p.[QboId], p.[RealmId], qc.[QboId], qc.[RealmId]
            FROM [qbo].[CustomerProject] cp
            JOIN [dbo].[Project] p ON p.[Id] = cp.[ProjectId]
            JOIN [qbo].[Customer] qc ON qc.[Id] = cp.[QboCustomerId]
            """
        )
        rows = cur.fetchall()
        divergent = []
        for qbo_customer_id, project_id, dbo_qbo_id, dbo_realm_id, staging_qbo_id, staging_realm_id in rows:
            if dbo_qbo_id != staging_qbo_id or (dbo_realm_id or "") != (staging_realm_id or ""):
                divergent.append(
                    f"ProjectId={project_id} (QboCustomerId={qbo_customer_id}): "
                    f"dbo.Project=(QboId={dbo_qbo_id!r}, realm={dbo_realm_id!r}) vs "
                    f"qbo.Customer=(QboId={staging_qbo_id!r}, realm={staging_realm_id!r})"
                )
        print(f"Mapping<->dbo.Project.QboId disagreement: {len(divergent)}")
        for line in divergent:
            print(f"  DIVERGENT: {line}")

        # dbo rows with QboId but no mapping row (orphan in one direction).
        cur.execute(
            """
            SELECT p.[Id], p.[QboId], p.[RealmId]
            FROM [dbo].[Project] p
            WHERE p.[QboId] IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM [qbo].[CustomerProject] cp WHERE cp.[ProjectId] = p.[Id])
            """
        )
        dbo_no_mapping = cur.fetchall()
        print(f"dbo.Project rows with QboId but no qbo.CustomerProject mapping: {len(dbo_no_mapping)}")
        for pid, qid, rid in dbo_no_mapping:
            print(f"  ORPHAN(dbo-only): ProjectId={pid} QboId={qid!r} realm={rid!r}")

        # Mapping rows with no corresponding dbo row (shouldn't happen, FK-enforced, but check).
        cur.execute(
            """
            SELECT cp.[Id], cp.[ProjectId], cp.[QboCustomerId]
            FROM [qbo].[CustomerProject] cp
            WHERE NOT EXISTS (SELECT 1 FROM [dbo].[Project] p WHERE p.[Id] = cp.[ProjectId])
            """
        )
        mapping_no_dbo = cur.fetchall()
        print(f"qbo.CustomerProject rows with no dbo.Project: {len(mapping_no_dbo)}")

        if divergent:
            failures.append(f"{len(divergent)} Project(s) diverge between the mapping table and dbo.Project.QboId")
        if dbo_no_mapping:
            failures.append(f"{len(dbo_no_mapping)} dbo.Project row(s) QboId-stamped with no mapping row")
        if mapping_no_dbo:
            failures.append(f"{len(mapping_no_dbo)} qbo.CustomerProject row(s) with no dbo.Project")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nPASS: {len(rows)}/{len(rows)} live equivalence, 0 disagreements, 0 orphans either direction.")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
