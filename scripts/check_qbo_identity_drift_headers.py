"""
Read-only drift detector for dbo-native QBO identity vs qbo.* mapping+staging (U-238a).

Compares dbo header QboId/RealmId/(SyncToken) against values reachable via the
mapping → staging join. Never writes — diagnostic only.

Usage:
  PYTHONPATH=. python scripts/check_qbo_identity_drift_headers.py
"""
from __future__ import annotations

import argparse
import logging

from integrations.intuit.qbo.base.identity_drift import (
    HEADER_ENTITY_SPECS,
    FlatEntitySpec,
    classify_qbo_identity_drift,
)
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("check_qbo_identity_drift_headers")

# U-355: qbo.BillBill is retired, so "bill"'s mapping-table JOIN below
# (_drift_query) would raise "invalid object name" if run. The spec row itself
# stays in HEADER_ENTITY_SPECS (reconciliation's own dbo-native reader still
# needs it — see identity_drift.py's comment there); this script just excludes
# it from its own mapping-table-drift working set, same disposition
# check_qbo_identity_drift_reference.py gives "bill_credit" (U-353).
_MAPPING_TABLE_RETIRED_KEYS = frozenset({"bill"})

# U-356: with the "invoice" row gone (qbo.InvoiceInvoice retired — the last header
# family), this working set is now EMPTY: every header family is dbo-native only and
# there is no mapping+staging pair left to drift-check against. Kept as a no-op
# (`--entity all` iterates nothing) pending its deletion as dead code.
ENTITY_SPECS = tuple(s for s in HEADER_ENTITY_SPECS if s.key not in _MAPPING_TABLE_RETIRED_KEYS)


def _drift_query(spec: FlatEntitySpec) -> str:
    sync_dbo = ", t.[SyncToken] AS dbo_sync_token" if spec.has_sync_token else ""
    sync_staging = ", s.[SyncToken] AS staging_sync_token" if spec.has_sync_token else ""
    return f"""
    SELECT
        t.[Id] AS dbo_id,
        t.[QboId] AS dbo_qbo_id,
        t.[RealmId] AS dbo_realm_id
        {sync_dbo},
        CASE WHEN m.[Id] IS NULL THEN 0 ELSE 1 END AS has_mapping,
        s.[QboId] AS staging_qbo_id,
        s.[RealmId] AS staging_realm_id
        {sync_staging}
    FROM dbo.[{spec.label}] t
    LEFT JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
    LEFT JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    WHERE t.[QboId] IS NOT NULL OR m.[Id] IS NOT NULL
    """


def _scan_entity(spec: FlatEntitySpec) -> dict[str, int]:
    counts = {"match": 0, "drift": 0, "pending_backfill": 0, "orphan_dbo_value": 0}
    drift_samples: list[str] = []
    orphan_samples: list[str] = []

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_drift_query(spec))
        cols = [c[0] for c in cur.description]
        for raw in cur.fetchall():
            row = dict(zip(cols, raw))
            classification = classify_qbo_identity_drift(
                dbo_qbo_id=row.get("dbo_qbo_id"),
                dbo_realm_id=row.get("dbo_realm_id"),
                dbo_sync_token=row.get("dbo_sync_token") if spec.has_sync_token else None,
                has_mapping=bool(row.get("has_mapping")),
                staging_qbo_id=row.get("staging_qbo_id"),
                staging_realm_id=row.get("staging_realm_id"),
                staging_sync_token=row.get("staging_sync_token") if spec.has_sync_token else None,
                has_sync_token=spec.has_sync_token,
            )
            counts[classification] += 1
            dbo_id = row["dbo_id"]
            if classification == "drift" and len(drift_samples) < 5:
                drift_samples.append(
                    f"dbo_id={dbo_id} dbo=({row.get('dbo_qbo_id')},{row.get('dbo_realm_id')}) "
                    f"staging=({row.get('staging_qbo_id')},{row.get('staging_realm_id')})"
                )
            if classification == "orphan_dbo_value" and len(orphan_samples) < 5:
                orphan_samples.append(
                    f"dbo_id={dbo_id} dbo_qbo_id={row.get('dbo_qbo_id')} (NO MAPPING)"
                )

    if drift_samples:
        logger.error("%s DRIFT samples: %s", spec.label, "; ".join(drift_samples))
    if orphan_samples:
        logger.error("%s ORPHAN dbo value samples: %s", spec.label, "; ".join(orphan_samples))

    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect dbo vs qbo identity drift (read-only).")
    ap.add_argument(
        "--entity",
        choices=[*(spec.key for spec in ENTITY_SPECS), "all"],
        default="all",
    )
    args = ap.parse_args()

    assert_cli_system_admin()

    specs = ENTITY_SPECS if args.entity == "all" else tuple(s for s in ENTITY_SPECS if s.key == args.entity)
    if not specs:
        # U-356: every header family is dbo-native only — there is no mapping+staging
        # pair left to drift-check. Say so loudly rather than print a "no drift"
        # summary that examined zero rows (a false green).
        logger.warning(
            "No header entity has a mapping table left to drift-check (all retired, "
            "U-350..U-356) — NOTHING was examined. This script is dead; see TODO.md."
        )
        return 0

    print("\n=== QBO identity drift summary (read-only) ===")
    print(f"{'Entity':<10} {'match':>8} {'drift':>8} {'pending':>8} {'orphan':>8}")
    print("-" * 46)

    total_drift = 0
    total_orphan = 0
    for spec in specs:
        counts = _scan_entity(spec)
        total_drift += counts["drift"]
        total_orphan += counts["orphan_dbo_value"]
        print(
            f"{spec.label:<10} {counts['match']:>8} {counts['drift']:>8} "
            f"{counts['pending_backfill']:>8} {counts['orphan_dbo_value']:>8}"
        )

    if total_drift:
        logger.error("Real drift detected across entities: %s row(s)", total_drift)
    if total_orphan:
        logger.error("Orphan dbo values (no mapping): %s row(s) — investigate dual-write bugs", total_orphan)
    if not total_drift and not total_orphan:
        logger.info("No drift or orphan dbo values detected.")
    return 1 if (total_drift or total_orphan) else 0


if __name__ == "__main__":
    raise SystemExit(main())
