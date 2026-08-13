"""
Backfill dbo-native QBO identity columns on five header entities from existing
qbo.* mapping + staging tables (U-238a).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY (SELECTs
only) and reports pre/post-flight counts. --apply stamps identity via the
Set*QboIdentity sprocs in batched loops — never writes to qbo.* tables.

Usage:
  PYTHONPATH=. python scripts/backfill_qbo_identity_headers.py
  PYTHONPATH=. python scripts/backfill_qbo_identity_headers.py --entity bill
  PYTHONPATH=. python scripts/backfill_qbo_identity_headers.py --apply --limit 100
"""
from __future__ import annotations

import argparse
import logging
from typing import Optional

from integrations.intuit.qbo.base.identity_drift import HEADER_ENTITY_SPECS, HeaderEntitySpec
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_qbo_identity_headers")


def _stamp_via_sproc(
    cursor,
    *,
    sproc: str,
    row_id: int,
    qbo_id: str,
    realm_id: str,
    sync_token: Optional[str],
    has_sync_token: bool,
) -> bool:
    """Stamp one row's identity. Returns True if the sproc reports it stole the
    (QboId, RealmId) pair from a different row (see the Stolen OUTPUT column)."""
    params = [row_id, qbo_id, realm_id]
    if has_sync_token:
        params.append(sync_token)
    placeholders = ", ".join("?" for _ in params)
    cursor.execute(f"EXEC {sproc} {placeholders}", params)
    row = cursor.fetchone()
    return bool(row and row.Stolen)


ENTITY_SPECS = {spec.key: spec for spec in HEADER_ENTITY_SPECS}


def _count_sql(spec: HeaderEntitySpec) -> str:
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
         )) AS unmapped_staging
    """


def _batch_select_sql(spec: HeaderEntitySpec, *, limit: int) -> str:
    sync_select = ", s.[SyncToken]" if spec.has_sync_token else ""
    return f"""
    SELECT TOP ({limit})
        t.[Id],
        s.[QboId],
        s.[RealmId]
        {sync_select}
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
    INNER JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    WHERE t.[QboId] IS NULL
    ORDER BY t.[Id]
    """


def _fetch_counts(cursor, spec: HeaderEntitySpec) -> dict:
    cursor.execute(_count_sql(spec))
    row = cursor.fetchone()
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _print_counts(spec: HeaderEntitySpec, counts: dict, *, prefix: str) -> None:
    print(
        f"{prefix} {spec.label}: "
        f"mapping={counts['mapping_count']} staging={counts['staging_count']} "
        f"eligible={counts['eligible']} stamped={counts['stamped']} "
        f"unmapped_staging={counts['unmapped_staging']}"
    )


def backfill_entity(
    spec: HeaderEntitySpec,
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
                    sync_token = getattr(row, "SyncToken", None) if spec.has_sync_token else None
                    if _stamp_via_sproc(
                        cur,
                        sproc=spec.sproc,
                        row_id=row.Id,
                        qbo_id=row.QboId,
                        realm_id=row.RealmId,
                        sync_token=sync_token,
                        has_sync_token=spec.has_sync_token,
                    ):
                        stolen += 1
                        logger.warning(
                            "%s: Id=%s stole (QboId=%s, RealmId=%s) from a different row — "
                            "a stale duplicate identity existed before this backfill",
                            spec.label, row.Id, row.QboId, row.RealmId,
                        )
                    applied += 1
                conn.commit()
                logger.info("%s: stamped batch of %s (total %s)", spec.label, len(rows), applied)
        if stolen:
            logger.warning("%s: %s row(s) required stealing identity from a stale duplicate — investigate", spec.label, stolen)

        post = _fetch_counts(cur, spec)
        _print_counts(spec, post, prefix="POST")

        if post["stamped"] != post["mapping_count"]:
            logger.warning(
                "%s: stamped (%s) != mapping_count (%s) — dbo rows without mappings stay NULL by design",
                spec.label,
                post["stamped"],
                post["mapping_count"],
            )
        else:
            logger.info("%s: stamped count matches mapping count (%s)", spec.label, post["stamped"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill dbo QBO identity columns (dry-run by default).")
    ap.add_argument(
        "--entity",
        choices=["bill", "expense", "invoice", "project", "company", "all"],
        default="all",
    )
    ap.add_argument("--apply", action="store_true", help="Write stamps via Set*QboIdentity sprocs.")
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=None, help="Max rows to stamp per entity (apply mode).")
    args = ap.parse_args()

    assert_cli_system_admin()

    keys = list(ENTITY_SPECS.keys()) if args.entity == "all" else [args.entity]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: QBO identity header backfill ===")
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
