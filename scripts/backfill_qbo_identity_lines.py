"""
Backfill dbo-native QBO identity columns on four line-item entities from existing
qbo.* mapping + staging tables (U-238b).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY (SELECTs
only) and reports pre/post-flight counts plus row-level verification. --apply
stamps identity via the Set*LineItemQboIdentity sprocs in batched loops — never
writes to qbo.* tables.

Usage:
  PYTHONPATH=. python scripts/backfill_qbo_identity_lines.py
  PYTHONPATH=. python scripts/backfill_qbo_identity_lines.py --entity bill_line_item
  PYTHONPATH=. python scripts/backfill_qbo_identity_lines.py --apply --limit 100
"""
from __future__ import annotations

import argparse
import logging
from typing import Optional

from integrations.intuit.qbo.base.identity_drift import LINE_ENTITY_SPECS, LineEntitySpec
from scripts.sync_helper import assert_cli_system_admin
from shared.database import call_procedure, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_qbo_identity_lines")


def _stamp_via_sproc(
    cursor,
    *,
    sproc: str,
    row_id: int,
    qbo_id: str,
    realm_id: str,
) -> bool:
    """Stamp one row's identity. Returns True if the sproc reports it stole the
    (QboId, RealmId) pair from a sibling under the same parent."""
    call_procedure(cursor, sproc, {"Id": row_id, "QboId": qbo_id, "RealmId": realm_id})
    row = cursor.fetchone()
    return bool(row and row.Stolen)


ENTITY_SPECS = {spec.key: spec for spec in LINE_ENTITY_SPECS}


def _count_sql(spec: LineEntitySpec) -> str:
    return f"""
    SELECT
        (SELECT COUNT(*) FROM qbo.[{spec.mapping_table}]) AS mapping_count,
        (SELECT COUNT(*) FROM qbo.[{spec.staging_table}]) AS staging_count,
        (SELECT COUNT(*)
         FROM dbo.[{spec.label}] t
         INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]) AS eligible,
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


def _batch_select_sql(spec: LineEntitySpec, *, limit: int) -> str:
    return f"""
    SELECT TOP ({limit})
        t.[Id],
        s.[QboLineId] AS QboId,
        sh.[RealmId] AS RealmId
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
    INNER JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    INNER JOIN qbo.[{spec.staging_header_table}] sh ON sh.[Id] = s.[{spec.staging_header_fk_col}]
    WHERE t.[QboId] IS NULL
    ORDER BY t.[Id]
    """


def _mismatch_sql(spec: LineEntitySpec) -> str:
    return f"""
    SELECT
        t.[Id],
        t.[QboId] AS dbo_qbo_id,
        s.[QboLineId] AS staging_qbo_id,
        t.[RealmId] AS dbo_realm_id,
        sh.[RealmId] AS staging_realm_id
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
    INNER JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    INNER JOIN qbo.[{spec.staging_header_table}] sh ON sh.[Id] = s.[{spec.staging_header_fk_col}]
    WHERE t.[QboId] IS NOT NULL
      AND (
            ISNULL(t.[QboId], '') <> ISNULL(s.[QboLineId], '')
         OR ISNULL(t.[RealmId], '') <> ISNULL(sh.[RealmId], '')
      )
    """


def _collision_sql(spec: LineEntitySpec) -> str:
    return f"""
    SELECT [{spec.parent_fk_col}], [QboId], COUNT(*) AS cnt
    FROM dbo.[{spec.label}]
    WHERE [QboId] IS NOT NULL
    GROUP BY [{spec.parent_fk_col}], [QboId]
    HAVING COUNT(*) > 1
    """


def _fetch_counts(cursor, spec: LineEntitySpec) -> dict:
    cursor.execute(_count_sql(spec))
    row = cursor.fetchone()
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _print_counts(spec: LineEntitySpec, counts: dict, *, prefix: str) -> None:
    print(
        f"{prefix} {spec.label}: "
        f"mapping={counts['mapping_count']} staging={counts['staging_count']} "
        f"eligible={counts['eligible']} stamped={counts['stamped']} "
        f"unmapped_staging={counts['unmapped_staging']} dangling={counts['dangling']}"
    )


def _verify_entity(cursor, spec: LineEntitySpec) -> tuple[bool, bool]:
    """Row-level identity match + per-parent collision checks. Returns (match_ok, collision_ok)."""
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
                "%s per-parent QboId collision parent_%s=%s QboId=%s count=%s",
                spec.label,
                spec.parent_fk_col,
                getattr(row, spec.parent_fk_col),
                row.QboId,
                row.cnt,
            )
        if len(collisions) > 10:
            logger.error("%s: ... and %s more collision group(s)", spec.label, len(collisions) - 10)

    print(
        f"  VERIFY {spec.label}: "
        f"identity_match={'PASS' if match_ok else 'FAIL'} "
        f"zero_collision={'PASS' if collision_ok else 'FAIL'}"
    )
    return match_ok, collision_ok


def backfill_entity(
    spec: LineEntitySpec,
    *,
    apply: bool,
    batch_size: int,
    limit: Optional[int],
) -> None:
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
                    if _stamp_via_sproc(
                        cur,
                        sproc=spec.sproc,
                        row_id=row.Id,
                        qbo_id=row.QboId,
                        realm_id=row.RealmId,
                    ):
                        stolen += 1
                        logger.warning(
                            "%s: Id=%s stole (QboId=%s, RealmId=%s) from a sibling under the "
                            "same parent — a stale duplicate identity existed before this backfill",
                            spec.label, row.Id, row.QboId, row.RealmId,
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

        if post["stamped"] != post["mapping_count"] - post["dangling"]:
            logger.warning(
                "%s: stamped (%s) != mapping_count - dangling (%s - %s) — unmapped dbo rows "
                "and dangling mappings stay NULL by design",
                spec.label,
                post["stamped"],
                post["mapping_count"],
                post["dangling"],
            )
        else:
            logger.info(
                "%s: stamped count matches live mapping rows (%s)",
                spec.label,
                post["stamped"],
            )

        _verify_entity(cur, spec)


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill dbo QBO line identity columns (dry-run by default).")
    ap.add_argument(
        "--entity",
        choices=[
            "bill_line_item",
            "invoice_line_item",
            "expense_line_item",
            "bill_credit_line_item",
            "all",
        ],
        default="all",
    )
    ap.add_argument("--apply", action="store_true", help="Write stamps via Set*LineItemQboIdentity sprocs.")
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=None, help="Max rows to stamp per entity (apply mode).")
    args = ap.parse_args()

    assert_cli_system_admin()

    keys = list(ENTITY_SPECS.keys()) if args.entity == "all" else [args.entity]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: QBO identity line-item backfill ===")
    for key in keys:
        print(f"\n--- {ENTITY_SPECS[key].label} ---")
        backfill_entity(
            ENTITY_SPECS[key],
            apply=args.apply,
            batch_size=args.batch_size,
            limit=args.limit,
        )


if __name__ == "__main__":
    main()
