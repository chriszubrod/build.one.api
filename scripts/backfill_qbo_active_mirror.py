"""
Backfill the dbo-native QboActive mirror on PaymentTerm/Vendor/SubCostCode
from qbo.{Term,Vendor,Item}.Active, joined via the already-live QboId/RealmId
columns (U-238c). (U-275)

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY
(SELECTs only) and reports the pending row count. --apply runs one
idempotent set-based UPDATE per table, then re-checks for zero remaining
mismatches. These are small tables (~1200 vendors / 6 terms / 475
sub-cost-codes) — a single set-based statement is the whole backfill, not
just its tail (per-row TCP-drop risk from feedback_backfill_setbased_under_load.md
applies to much larger backfills; not a concern at this scale).

Usage:
  PYTHONPATH=. python scripts/backfill_qbo_active_mirror.py
  PYTHONPATH=. python scripts/backfill_qbo_active_mirror.py --entity vendor
  PYTHONPATH=. python scripts/backfill_qbo_active_mirror.py --apply
"""
from __future__ import annotations

import argparse
import logging

from integrations.intuit.qbo.base.identity_drift import FlatEntitySpec, REFERENCE_ENTITY_SPECS
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_qbo_active_mirror")

# The 3 reference entities this backfill covers. Reuses REFERENCE_ENTITY_SPECS
# (identity_drift.py) — the single source of truth for dbo<->qbo topology —
# rather than a second hand-copied entity table; only .label/.staging_table
# are used here (mapping_table/dbo_fk_col/etc. don't apply to this flat
# dbo<->qbo.Active join).
_ACTIVE_MIRROR_KEYS = frozenset({"vendor", "payment_term", "sub_cost_code"})
SPECS_BY_KEY = {s.key: s for s in REFERENCE_ENTITY_SPECS if s.key in _ACTIVE_MIRROR_KEYS}

# NULL-safe inequality (dbo.QboActive vs qbo.*.Active) — BIT columns have no
# native IS DISTINCT FROM in T-SQL.
_MISMATCH_PREDICATE = """
    t.[QboId] IS NOT NULL
    AND (
          (t.[QboActive] IS NULL AND s.[Active] IS NOT NULL)
       OR (t.[QboActive] IS NOT NULL AND s.[Active] IS NULL)
       OR (t.[QboActive] <> s.[Active])
    )
"""


def _pending_sql(spec: FlatEntitySpec) -> str:
    return f"""
    SELECT COUNT(*)
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.staging_table}] s
        ON s.[QboId] = t.[QboId] AND s.[RealmId] = t.[RealmId]
    WHERE {_MISMATCH_PREDICATE}
    """


def _update_sql(spec: FlatEntitySpec) -> str:
    return f"""
    UPDATE t
    SET t.[QboActive] = s.[Active], t.[ModifiedDatetime] = SYSUTCDATETIME()
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.staging_table}] s
        ON s.[QboId] = t.[QboId] AND s.[RealmId] = t.[RealmId]
    WHERE {_MISMATCH_PREDICATE}
    """


def _mismatch_sample_sql(spec: FlatEntitySpec) -> str:
    return f"""
    SELECT TOP 10 t.[Id], t.[QboActive], s.[Active] AS StagingActive
    FROM dbo.[{spec.label}] t
    INNER JOIN qbo.[{spec.staging_table}] s
        ON s.[QboId] = t.[QboId] AND s.[RealmId] = t.[RealmId]
    WHERE {_MISMATCH_PREDICATE}
    """


def backfill_entity(spec: FlatEntitySpec, *, apply: bool) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_pending_sql(spec))
        pending = cur.fetchone()[0]
        print(f"  {spec.label}: {pending} row(s) {'would update' if not apply else 'to update'}")

        if not apply:
            return True

        if pending:
            cur.execute(_update_sql(spec))
            updated = cur.rowcount
            conn.commit()
            print(f"  {spec.label}: updated {updated} row(s)")

        cur.execute(_pending_sql(spec))
        remaining = cur.fetchone()[0]
        ok = remaining == 0
        print(f"  {spec.label}: VERIFY parity={'PASS' if ok else 'FAIL'} (remaining={remaining})")
        if not ok:
            cur.execute(_mismatch_sample_sql(spec))
            for row in cur.fetchall():
                logger.error(
                    "%s Id=%s QboActive=%s staging.Active=%s still mismatched",
                    spec.label, row.Id, row.QboActive, row.StagingActive,
                )
        return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Backfill dbo.{Vendor,PaymentTerm,SubCostCode}.QboActive from "
            "qbo.{Vendor,Term,Item}.Active (dry-run by default)."
        )
    )
    ap.add_argument("--entity", choices=list(SPECS_BY_KEY.keys()) + ["all"], default="all")
    ap.add_argument("--apply", action="store_true", help="Run the UPDATE (default: dry-run/read-only).")
    args = ap.parse_args()

    assert_cli_system_admin()

    keys = list(SPECS_BY_KEY.keys()) if args.entity == "all" else [args.entity]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: QboActive mirror backfill ===")

    all_ok = True
    for key in keys:
        print(f"\n--- {SPECS_BY_KEY[key].label} ---")
        all_ok = backfill_entity(SPECS_BY_KEY[key], apply=args.apply) and all_ok

    if not all_ok:
        logger.error("Backfill verification FAILED for one or more entities")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
