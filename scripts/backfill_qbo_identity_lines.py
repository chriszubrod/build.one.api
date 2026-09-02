"""
Backfill dbo-native QBO identity columns on four line-item entities from existing
qbo.* mapping + staging tables (U-238b).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY (SELECTs
only) and reports pre/post-flight counts plus row-level verification. --apply
stamps identity via the Set*LineItemQboIdentity sprocs in batched loops — never
writes to qbo.* tables.

Two modes (--mode):
  missing     (default) dbo QboId is still NULL — the original U-238b gap.
  realm-only  (U-293-dw) dbo QboId is ALREADY stamped but RealmId is NULL — the
              write-side dual-write gap (stamp_line_identity_or_warn's caller
              had qbo_id but not realm_id at write time). Re-stamps BOTH
              columns via the same sproc, using the row's own already-correct
              QboId plus the RealmId now resolved from staging — a no-op on
              QboId, a real write on RealmId.

Usage:
  PYTHONPATH=. python scripts/backfill_qbo_identity_lines.py
  PYTHONPATH=. python scripts/backfill_qbo_identity_lines.py --entity bill_line_item
  PYTHONPATH=. python scripts/backfill_qbo_identity_lines.py --apply --limit 100
  PYTHONPATH=. python scripts/backfill_qbo_identity_lines.py --mode realm-only --entity bill_line_item
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
) -> tuple[bool, Optional[str]]:
    """Stamp one row's identity. Returns (stolen, missing).

    `stolen` is True if the sproc reports it stole the (QboId, RealmId) pair
    from a sibling under the same parent. `missing` names which column(s) are
    still NULL after the call — None means the row ended up complete. The
    sproc's own U-293-dw atomic-pair guard can silently no-op the QboId write
    (leaving it unchanged) when neither this call's realm_id nor the row's own
    existing RealmId is resolvable; separately, staging can hand this function
    a NULL qbo_id to begin with (e.g. a synthetic staging line). The sproc
    returns the row's TRUE post-call state (not an echo of the input params),
    so this reads the real outcome — and which specific column caused it —
    rather than assuming the call succeeded just because it didn't raise."""
    call_procedure(cursor, sproc, {"Id": row_id, "QboId": qbo_id, "RealmId": realm_id})
    row = cursor.fetchone()
    if not row:
        return False, "QboId and RealmId"
    missing_cols = [
        name for name, value in (("QboId", row.QboId), ("RealmId", row.RealmId)) if value is None
    ]
    missing = " and ".join(missing_cols) if missing_cols else None
    return bool(row.Stolen), missing


ENTITY_SPECS = {spec.key: spec for spec in LINE_ENTITY_SPECS}


def _pending_join_for_mode(spec: LineEntitySpec, mode: str, *, after_id: Optional[int] = None) -> str:
    """The FROM/JOIN/WHERE fragment defining a 'pending' (backfill-eligible)
    row for the given mode:
      missing     a dbo line with a real mapping, whose staging line AND
                  staging header both still resolve, but whose dbo QboId is
                  still NULL.
      realm-only  (U-293-dw) the dbo line already has a QboId (the write-side
                  dual-write partially stamped it) but RealmId is NULL, and
                  staging can resolve a real RealmId to fill it in.

    Single source of truth for `_count_sql`'s `pending` metric AND
    `_batch_select_sql`'s own row selection (U-293 Gate-2 finding: they used
    to diverge for "missing" mode — `pending` only required the mapping row
    to exist, while `_batch_select_sql` also required the staging line +
    staging header rows to resolve, so a mapping row with a dangling
    staging-side reference was counted as pending but could never actually be
    returned/stamped, silently overstating the preview count). Sharing this
    fragment makes that class of drift impossible by construction.

    `after_id`, when given, adds an `Id` cursor bound (see `_batch_select_sql`
    for why — the apply loop needs it, `_count_sql`'s whole-set snapshot does
    not, so it's opt-in and defaults to no bound).
    """
    where = (
        "t.[QboId] IS NULL"
        if mode == "missing"
        else "t.[QboId] IS NOT NULL AND t.[RealmId] IS NULL AND sh.[RealmId] IS NOT NULL"
    )
    cursor_clause = f" AND t.[Id] > {int(after_id)}" if after_id is not None else ""
    return f"""
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
    INNER JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    INNER JOIN qbo.[{spec.staging_header_table}] sh ON sh.[Id] = s.[{spec.staging_header_fk_col}]
    WHERE {where}{cursor_clause}
    """


def _count_sql(spec: LineEntitySpec, mode: str = "missing") -> str:
    return f"""
    SELECT
        (SELECT COUNT(*) FROM qbo.[{spec.mapping_table}]) AS mapping_count,
        (SELECT COUNT(*) FROM qbo.[{spec.staging_table}]) AS staging_count,
        (SELECT COUNT(*)
         FROM dbo.[{spec.label}] t
         INNER JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]) AS eligible,
        (SELECT COUNT(*) FROM dbo.[{spec.label}] WHERE [QboId] IS NOT NULL) AS stamped,
        (SELECT COUNT(*) {_pending_join_for_mode(spec, mode)}) AS pending,
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


def _batch_select_sql(
    spec: LineEntitySpec, *, limit: int, mode: str = "missing", after_id: Optional[int] = None
) -> str:
    # realm-only mode must select the dbo row's OWN QboId (t.[QboId]), not the
    # staging value (s.[QboLineId]) — the two are expected to already agree for
    # a row in this mode's eligible set (QboId IS NOT NULL), but stamping back
    # the staging value regardless would silently "fix" a real QboId mismatch
    # as a side effect of what's supposed to be a realm-only backfill, masking
    # a drift that _mismatch_sql exists specifically to surface instead.
    # Re-stamping t.[QboId] onto itself is a guaranteed no-op either way.
    qbo_id_col = "t.[QboId]" if mode == "realm-only" else "s.[QboLineId]"
    # `after_id` is an Id cursor, not just a LIMIT: a row the sproc's atomic-pair
    # guard permanently can't complete (U-293-dw) never leaves the "pending" WHERE
    # clause, so re-querying from the top on every batch would re-select it
    # forever, starving genuinely-fixable rows of this run's `would_apply` budget.
    # ORDER BY t.[Id] + `after_id` = last Id seen guarantees each row is visited
    # at most once per run regardless of outcome.
    return f"""
    SELECT TOP ({limit})
        t.[Id],
        {qbo_id_col} AS QboId,
        sh.[RealmId] AS RealmId
    {_pending_join_for_mode(spec, mode, after_id=after_id)}
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


def _fetch_counts(cursor, spec: LineEntitySpec, mode: str = "missing") -> dict:
    cursor.execute(_count_sql(spec, mode))
    row = cursor.fetchone()
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _print_counts(spec: LineEntitySpec, counts: dict, *, prefix: str) -> None:
    print(
        f"{prefix} {spec.label}: "
        f"mapping={counts['mapping_count']} staging={counts['staging_count']} "
        f"eligible={counts['eligible']} stamped={counts['stamped']} pending={counts['pending']} "
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
    mode: str = "missing",
) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        pre = _fetch_counts(cur, spec, mode=mode)
        _print_counts(spec, pre, prefix="PRE ")

        # U-293: was `max(0, pre["eligible"] - pre["stamped"])` — a naive count
        # difference that goes negative (clamped to 0, silently masking real
        # pending rows) whenever "stamped but no mapping row" anomaly rows exist
        # alongside genuine "mapped but never stamped" ones (both counted in
        # `stamped`/`eligible` independently, so they don't cancel out
        # arithmetically the way the subtraction assumed). `pending` is now a
        # direct COUNT matching _batch_select_sql's own WHERE/JOIN criteria
        # exactly, so the preview and the actual apply loop can never disagree.
        pending = pre["pending"]
        would_apply = pending if limit is None else min(pending, limit)
        print(f"  -> {'WOULD stamp' if not apply else 'Stamping'} up to {would_apply} row(s)")

        processed = 0  # DISTINCT rows fetched/attempted this run — drives pagination, NOT success
        stolen = 0
        guard_blocked_ids = []
        # Id-cursor pagination (t.[Id] > after_id, ORDER BY t.[Id]), not a plain
        # re-query of the live pending set: since the sproc's atomic-pair guard
        # (added this unit) can permanently no-op a row (its staging RealmId is
        # unresolvable and it has none of its own), that row never leaves the
        # "pending" WHERE clause — a re-query-from-the-top would re-select it on
        # every batch, displacing genuinely-fixable rows and under-delivering
        # against `would_apply` within a single run. The cursor guarantees each
        # row is visited at most once per run regardless of outcome.
        after_id = None
        if apply and would_apply > 0:
            while processed < would_apply:
                fetch_n = min(batch_size, would_apply - processed)
                cur.execute(_batch_select_sql(spec, limit=fetch_n, mode=mode, after_id=after_id))
                rows = cur.fetchall()
                if not rows:
                    break
                for row in rows:
                    row_stolen, row_missing = _stamp_via_sproc(
                        cur,
                        sproc=spec.sproc,
                        row_id=row.Id,
                        qbo_id=row.QboId,
                        realm_id=row.RealmId,
                    )
                    if row_stolen:
                        stolen += 1
                        logger.warning(
                            "%s: Id=%s stole (QboId=%s, RealmId=%s) from a sibling under the "
                            "same parent — a stale duplicate identity existed before this backfill",
                            spec.label, row.Id, row.QboId, row.RealmId,
                        )
                    if row_missing is not None:
                        # Still incomplete after the call — either the sproc's atomic-pair
                        # guard no-op'd the QboId write (realm unresolvable), or staging
                        # itself had a NULL value to offer (e.g. a synthetic line). Either
                        # way this is NOT a success — must not be counted as completed or
                        # the run would misreport progress it didn't make.
                        guard_blocked_ids.append(row.Id)
                        logger.warning(
                            "%s: Id=%s not stamped — %s still unresolved after this call; "
                            "still pending, needs separate investigation",
                            spec.label, row.Id, row_missing,
                        )
                    processed += 1
                after_id = rows[-1].Id  # ORDER BY t.[Id] guarantees this is the max seen
                conn.commit()
                logger.info(
                    "%s: processed batch of %s (%s completed, %s guard-blocked, total processed %s)",
                    spec.label, len(rows), processed - len(guard_blocked_ids), len(guard_blocked_ids), processed,
                )
        if stolen:
            logger.warning(
                "%s: %s row(s) required stealing identity from a stale duplicate — investigate",
                spec.label,
                stolen,
            )
        if guard_blocked_ids:
            logger.warning(
                "%s: %s row(s) attempted but not stamped (Ids: %s) — a real data gap, not a bug; "
                "these stay pending and need a subsequent run once their staging data resolves",
                spec.label,
                len(guard_blocked_ids),
                guard_blocked_ids[:20],
            )

        post = _fetch_counts(cur, spec, mode=mode)
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
            # U-361: "bill_credit_line_item" removed — its LineEntitySpec row is
            # gone from identity_drift.py (mapping table retired; dbo-native only).
            "all",
        ],
        default="all",
    )
    ap.add_argument("--apply", action="store_true", help="Write stamps via Set*LineItemQboIdentity sprocs.")
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=None, help="Max rows to stamp per entity (apply mode).")
    ap.add_argument(
        "--mode",
        choices=["missing", "realm-only"],
        default="missing",
        help="missing: dbo QboId is NULL (original U-238b gap). "
             "realm-only: dbo QboId is set but RealmId is NULL (U-293-dw write-side gap).",
    )
    args = ap.parse_args()

    assert_cli_system_admin()

    keys = list(ENTITY_SPECS.keys()) if args.entity == "all" else [args.entity]
    run_mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {run_mode} ({args.mode}): QBO identity line-item backfill ===")
    for key in keys:
        print(f"\n--- {ENTITY_SPECS[key].label} ---")
        backfill_entity(
            ENTITY_SPECS[key],
            apply=args.apply,
            batch_size=args.batch_size,
            limit=args.limit,
            mode=args.mode,
        )


if __name__ == "__main__":
    main()
