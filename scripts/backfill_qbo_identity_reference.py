"""
Backfill dbo-native QBO identity columns on the reference entities (see
REFERENCE_ENTITY_SPECS) from existing qbo.* mapping + staging tables (U-238c).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY (SELECTs
only) and reports pre/post-flight counts plus row-level verification. --apply
stamps identity via the Set*QboIdentity sprocs in batched loops — and never writes
to qbo.* tables at all (U-534 removed the last exception, the Address Stage 0
qbo.PhysicalAddress.RealmId backfill, ahead of that table being dropped).

Usage:
  PYTHONPATH=. python scripts/backfill_qbo_identity_reference.py
  PYTHONPATH=. python scripts/backfill_qbo_identity_reference.py --entity payment_term
  PYTHONPATH=. python scripts/backfill_qbo_identity_reference.py --apply --limit 100
"""
from __future__ import annotations

import argparse
import logging
from typing import Optional

from integrations.intuit.qbo.base.identity_drift import REFERENCE_ENTITY_SPECS, FlatEntitySpec
from integrations.intuit.qbo.base.identity_fanout import check_all_fanout_overlaps
from scripts.sync_helper import assert_cli_system_admin
from shared.database import call_procedure, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_qbo_identity_reference")

# U-353: qbo.VendorCreditBillCredit is retired, so "bill_credit"'s mapping-table
# JOINs below would raise "invalid object name" if run. The spec row itself stays
# in REFERENCE_ENTITY_SPECS (reconciliation's own dbo-native reader still needs
# it — see identity_drift.py's comment there); this backfill tool just excludes it
# from its own working set — there is nothing left to backfill FROM for this family.
ENTITY_SPECS = {spec.key: spec for spec in REFERENCE_ENTITY_SPECS if spec.key != "bill_credit"}








def _stamp_via_sproc(
    cursor,
    *,
    sproc: str,
    row_id: int,
    qbo_id: str,
    realm_id: str,
) -> bool:
    call_procedure(cursor, sproc, {"Id": row_id, "QboId": qbo_id, "RealmId": realm_id})
    row = cursor.fetchone()
    return bool(row and row.Stolen)


def _count_sql(spec: FlatEntitySpec) -> str:
    return f"""
    SELECT
        (SELECT COUNT(*) FROM qbo.[{spec.mapping_table}]) AS mapping_count,
        (SELECT COUNT(*) FROM qbo.[{spec.staging_table}]) AS staging_count,
        (SELECT COUNT(*)
         FROM dbo.[{spec.label}] t
         INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
         INNER JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
         WHERE s.[QboId] IS NOT NULL AND s.[RealmId] IS NOT NULL) AS eligible,
        (SELECT COUNT(*) FROM dbo.[{spec.label}] WHERE [QboId] IS NOT NULL) AS stamped,
        (SELECT COUNT(*)
         FROM qbo.[{spec.staging_table}] s
         WHERE NOT EXISTS (
             SELECT 1 FROM qbo.[{spec.mapping_table}] m WHERE m.[{spec.staging_fk_col}] = s.[Id]
         )) AS unmapped_staging,
        (SELECT COUNT(*)
         FROM qbo.[{spec.mapping_table}] m
         WHERE NOT EXISTS (
             SELECT 1 FROM dbo.[{spec.label}] t WHERE t.[Id] = m.[{spec.dbo_fk_col}]
         )) AS dangling
    """


def _batch_select_sql(spec: FlatEntitySpec, *, limit: int) -> str:
    return f"""
    SELECT TOP ({limit})
        t.[Id],
        s.[QboId],
        s.[RealmId]
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
    INNER JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    WHERE t.[QboId] IS NULL
      AND s.[QboId] IS NOT NULL
      AND s.[RealmId] IS NOT NULL
    ORDER BY t.[Id]
    """


def _mismatch_sql(spec: FlatEntitySpec) -> str:
    return f"""
    SELECT
        t.[Id],
        t.[QboId] AS dbo_qbo_id,
        s.[QboId] AS staging_qbo_id,
        t.[RealmId] AS dbo_realm_id,
        s.[RealmId] AS staging_realm_id
    FROM qbo.[{spec.mapping_table}] m
    INNER JOIN dbo.[{spec.label}] t ON t.[Id] = m.[{spec.dbo_fk_col}]
    INNER JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    WHERE s.[QboId] IS NOT NULL
      AND s.[RealmId] IS NOT NULL
      AND (
            t.[QboId] IS NULL
         OR ISNULL(t.[RealmId], '') <> ISNULL(s.[RealmId], '')
         OR ISNULL(t.[QboId], '') <> ISNULL(s.[QboId], '')
      )
    """


def _collision_sql(spec: FlatEntitySpec) -> str:
    return f"""
    SELECT [QboId], [RealmId], COUNT(*) AS cnt
    FROM dbo.[{spec.label}]
    WHERE [QboId] IS NOT NULL
    GROUP BY [QboId], [RealmId]
    HAVING COUNT(*) > 1
    """


def _fetch_counts(cursor, spec: FlatEntitySpec) -> dict:
    cursor.execute(_count_sql(spec))
    row = cursor.fetchone()
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _print_counts(spec: FlatEntitySpec, counts: dict, *, prefix: str) -> None:
    extra = ""
    if spec.key == "attachment":
        extra = (
            f" (note: {counts['unmapped_staging']} unmapped qbo.Attachable rows are "
            f"EXPECTED by QBO design — same file on multiple entities mints duplicate "
            f"Attachables; only one can bind a mapping)"
        )
    print(
        f"{prefix} {spec.label}: "
        f"mapping={counts['mapping_count']} staging={counts['staging_count']} "
        f"eligible={counts['eligible']} stamped={counts['stamped']} "
        f"unmapped_staging={counts['unmapped_staging']} dangling={counts['dangling']}"
        f"{extra}"
    )


def _verify_entity(cursor, spec: FlatEntitySpec) -> bool:
    match_ok = True
    cursor.execute(_mismatch_sql(spec))
    mismatches = cursor.fetchall()
    if mismatches:
        match_ok = False
        for row in mismatches[:10]:
            logger.error(
                "%s identity mismatch Id=%s dbo=(qbo=%s realm=%s) staging=(qbo=%s realm=%s)",
                spec.label,
                row.Id,
                row.dbo_qbo_id,
                row.dbo_realm_id,
                row.staging_qbo_id,
                row.staging_realm_id,
            )
        if len(mismatches) > 10:
            logger.error("%s: ... and %s more mismatch(es)", spec.label, len(mismatches) - 10)

    collision_ok = True
    cursor.execute(_collision_sql(spec))
    collisions = cursor.fetchall()
    if collisions:
        collision_ok = False
        for row in collisions[:10]:
            logger.error(
                "%s QboId collision QboId=%s RealmId=%s count=%s",
                spec.label,
                row.QboId,
                row.RealmId,
                row.cnt,
            )
        if len(collisions) > 10:
            logger.error("%s: ... and %s more collision group(s)", spec.label, len(collisions) - 10)

    print(
        f"  VERIFY {spec.label}: "
        f"identity_match={'PASS' if match_ok else 'FAIL'} "
        f"zero_collision={'PASS' if collision_ok else 'FAIL'}"
    )
    return match_ok and collision_ok




def backfill_entity(
    spec: FlatEntitySpec,
    *,
    apply: bool,
    batch_size: int,
    limit: Optional[int],
) -> bool:

    with get_connection() as conn:
        cur = conn.cursor()
        pre = _fetch_counts(cur, spec)
        _print_counts(spec, pre, prefix="PRE ")

        pending = max(0, pre["eligible"] - pre["stamped"])
        would_apply = pending if limit is None else min(pending, limit)
        print(f"  -> {'WOULD stamp' if not apply else 'Stamping'} up to {would_apply} row(s)")

        applied = 0
        stolen = 0
        if apply and would_apply > 0:
            while applied < would_apply:
                fetch_n = min(batch_size, would_apply - applied)
                cur.execute(_batch_select_sql(spec, limit=fetch_n))
                rows = cur.fetchall()
                if not rows:
                    break
                for row in rows:
                    if not row.RealmId:
                        logger.warning(
                            "%s: skipping Id=%s — staging RealmId is NULL (Address: run Stage 0 first)",
                            spec.label,
                            row.Id,
                        )
                        continue
                    if _stamp_via_sproc(
                        cur,
                        sproc=spec.sproc,
                        row_id=row.Id,
                        qbo_id=row.QboId,
                        realm_id=row.RealmId,
                    ):
                        stolen += 1
                        logger.warning(
                            "%s: Id=%s stole (QboId=%s, RealmId=%s) from a different row",
                            spec.label,
                            row.Id,
                            row.QboId,
                            row.RealmId,
                        )
                    applied += 1
                conn.commit()
                logger.info("%s: stamped batch of %s (total %s)", spec.label, len(rows), applied)
        if stolen:
            logger.warning(
                "%s: %s row(s) required stealing identity from a stale duplicate — investigate",
                spec.label,
                stolen,
            )

        post = _fetch_counts(cur, spec)
        _print_counts(spec, post, prefix="POST")

        return _verify_entity(cur, spec)


def _run_fanout_checks() -> bool:
    print("\n--- Fan-out cross-table overlap checks ---")
    results = check_all_fanout_overlaps()
    customer_ok = results["customer_project"]
    item_ok = results["cost_code_sub_cost_code"]
    print(
        f"  Customer+Project overlap: {'PASS' if customer_ok else 'FAIL'}\n"
        f"  CostCode+SubCostCode overlap: {'PASS' if item_ok else 'FAIL'}"
    )
    return customer_ok and item_ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill dbo QBO identity columns on reference entities (dry-run by default)."
    )
    ap.add_argument(
        "--entity",
        choices=list(ENTITY_SPECS.keys()) + ["all"],
        default="all",
    )
    ap.add_argument("--apply", action="store_true", help="Write stamps via Set*QboIdentity sprocs.")
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=None, help="Max rows to stamp per entity (apply mode).")
    args = ap.parse_args()

    assert_cli_system_admin()

    keys = list(ENTITY_SPECS.keys()) if args.entity == "all" else [args.entity]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: QBO identity reference-entity backfill ===")
    all_ok = True
    for key in keys:
        print(f"\n--- {ENTITY_SPECS[key].label} ---")
        entity_ok = backfill_entity(
            ENTITY_SPECS[key],
            apply=args.apply,
            batch_size=args.batch_size,
            limit=args.limit,
        )
        all_ok = all_ok and entity_ok

    # U-325: customer/cost_code/sub_cost_code (the only per-entity keys that used to
    # trigger this alongside "all") were removed from REFERENCE_ENTITY_SPECS, so they're
    # no longer valid --entity choices — argparse rejects them before main() ever runs.
    run_fanout = args.entity == "all"
    fanout_ok = True
    if run_fanout:
        fanout_ok = _run_fanout_checks()
        if not fanout_ok:
            logger.error("Fan-out overlap check FAILED — investigate before certifying Phase 2")

    if not all_ok:
        logger.error("Per-entity verification FAILED — identity mismatch or QboId collision detected")
    if not all_ok or not fanout_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
