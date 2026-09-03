"""
Read-only drift detector for dbo-native QBO identity vs qbo.* mapping+staging (U-238b).

Compares dbo line-item QboId/RealmId against values reachable via the mapping →
staging line → staging header join (RealmId lives on the header). Never writes —
diagnostic only.

Dangling mapping rows (mapping exists but dbo row deleted) are expected and are
NOT counted here — this query is anchored on dbo.[Entity], so rows with no dbo
line item never appear.

Usage:
  PYTHONPATH=. python scripts/check_qbo_identity_drift_lines.py
"""
from __future__ import annotations

import argparse
import logging

from integrations.intuit.qbo.base.identity_drift import (
    LINE_ENTITY_SPECS,
    LineEntitySpec,
    classify_qbo_identity_drift,
)
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("check_qbo_identity_drift_lines")


def _drift_query(spec: LineEntitySpec) -> str:
    return f"""
    SELECT
        t.[Id] AS dbo_id,
        t.[QboId] AS dbo_qbo_id,
        t.[RealmId] AS dbo_realm_id,
        CASE WHEN m.[Id] IS NULL THEN 0 ELSE 1 END AS has_mapping,
        s.[QboLineId] AS staging_qbo_id,
        sh.[RealmId] AS staging_realm_id
    FROM dbo.[{spec.label}] t
    LEFT JOIN qbo.[{spec.mapping_table}] m ON m.[{spec.dbo_fk_col}] = t.[Id]
    LEFT JOIN qbo.[{spec.staging_table}] s ON s.[Id] = m.[{spec.staging_fk_col}]
    LEFT JOIN qbo.[{spec.staging_header_table}] sh ON sh.[Id] = s.[{spec.staging_header_fk_col}]
    WHERE t.[QboId] IS NOT NULL OR m.[Id] IS NOT NULL
    """


def _scan_entity(spec: LineEntitySpec) -> dict[str, int]:
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
                dbo_sync_token=None,
                has_mapping=bool(row.get("has_mapping")),
                staging_qbo_id=row.get("staging_qbo_id"),
                staging_realm_id=row.get("staging_realm_id"),
                staging_sync_token=None,
                has_sync_token=False,
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
    ap = argparse.ArgumentParser(description="Detect dbo vs qbo line identity drift (read-only).")
    ap.add_argument(
        "--entity",
        choices=[
            "bill_line_item",
            "expense_line_item",
            # U-361: "bill_credit_line_item" removed — its LineEntitySpec row is
            # gone from identity_drift.py (mapping table retired; dbo-native only).
            # U-362b: "invoice_line_item" restored (see backfill_qbo_identity_
            # lines.py's identical note). TEMPORARY: re-remove once /em's
            # post-backfill DROP lands.
            "invoice_line_item",
            "all",
        ],
        default="all",
    )
    args = ap.parse_args()

    assert_cli_system_admin()

    specs = LINE_ENTITY_SPECS if args.entity == "all" else tuple(s for s in LINE_ENTITY_SPECS if s.key == args.entity)

    print("\n=== QBO identity drift summary (read-only, line items) ===")
    print(f"{'Entity':<20} {'match':>8} {'drift':>8} {'pending':>8} {'orphan':>8}")
    print("-" * 56)

    total_drift = 0
    total_orphan = 0
    for spec in specs:
        counts = _scan_entity(spec)
        total_drift += counts["drift"]
        total_orphan += counts["orphan_dbo_value"]
        print(
            f"{spec.label:<20} {counts['match']:>8} {counts['drift']:>8} "
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
