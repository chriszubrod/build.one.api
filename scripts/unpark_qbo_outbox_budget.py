"""
Unpark rows in `[qbo].[Outbox]` that were frozen by the monthly QBO API
budget breaker (U-211) so the drain can pick them up immediately.

Operator use cases:
  - The monthly call budget was raised (`QBO_MONTHLY_CALL_BUDGET`) and new
    calls flow, but rows parked mid-month stay frozen until the 1st.
  - Budget enforcement was disabled (`QBO_BUDGET_ENFORCE=false`) and the
    breaker no longer blocks, but already-parked rows still sleep.
  - Intuit tier was upgraded (higher cap) and the breaker won't trip again,
    yet parked rows wait for their original NextRetryAt.

Safety:
  - Dry-run by default. Pass --apply to actually mutate rows.
  - Selects only rows whose LastError begins with the budget-park prefix —
    this prefix match is LOAD-BEARING: it distinguishes a budget-parked row
    from a row legitimately sleeping out its exponential-backoff retry window.
    Without it this script would yank healthy rows out of their backoff.
  - Does NOT clear LastError (audit trail) or change Status (already 'failed',
    which the drain claims). Only unpins NextRetryAt to now.
  - --reset-attempts is optional and NOT the default: a park burns one attempt
    via mark_failed even though the row is healthy, but parks are rare
    (drain_once skips claiming entirely while the breaker is tripped, so a
    park only happens when the breaker trips mid-handler), so the attempt burn
    is normally negligible — reset only if a row is close to the 5-attempt
    dead-letter ceiling.

Usage:
  python scripts/unpark_qbo_outbox_budget.py                    # dry-run
  python scripts/unpark_qbo_outbox_budget.py --apply            # unpark all matched
  python scripts/unpark_qbo_outbox_budget.py --apply --reset-attempts
  python scripts/unpark_qbo_outbox_budget.py --limit 50 --apply
"""
# Python Standard Library Imports
import argparse
import sys
from datetime import datetime, timezone

# Local Imports — path dance so the script can be run from the repo root.
sys.path.insert(0, ".")
from shared.database import get_connection

# LOAD-BEARING prefix: distinguishes budget-parked rows from rows legitimately
# sleeping out their exponential-backoff retry window.
PARKED_LAST_ERROR_PREFIX = "Parked: monthly QBO API budget exhausted%"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unpark qbo.Outbox rows frozen by the monthly QBO API budget breaker."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate rows. Without this flag the script is read-only.",
    )
    parser.add_argument(
        "--reset-attempts",
        action="store_true",
        help="Also reset Attempts=0. Not the default — see module docstring.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Cap the number of rows unparked (default 1000).",
    )
    args = parser.parse_args()

    with get_connection() as conn:
        cur = conn.cursor()

        # 1. Show what would be affected.
        cur.execute(
            f"""
            SELECT TOP ({args.limit})
                Id, Kind, EntityType, EntityPublicId, Attempts,
                CONVERT(VARCHAR(19), NextRetryAt, 120) AS NextRetryAt,
                LEFT(LastError, 120) AS LastError
            FROM qbo.Outbox
            WHERE Status = 'failed'
              AND NextRetryAt > SYSUTCDATETIME()
              AND LastError LIKE ?
            ORDER BY Id
            """,
            PARKED_LAST_ERROR_PREFIX,
        )
        rows = cur.fetchall()
        if not rows:
            print("No budget-parked rows matched. Nothing to do.")
            return 0

        print(f"Found {len(rows)} budget-parked row(s):")
        print()
        for r in rows:
            print(
                f"  Id={r[0]:>6}  Kind={r[1]:<25} Entity={r[2]:<15} "
                f"EntityPID={str(r[3])[:8]} Attempts={r[4]} "
                f"NextRetry={r[5]} Err={r[6]}"
            )
        print()

        if not args.apply:
            print("DRY-RUN: no rows modified. Re-run with --apply to unpark these rows.")
            return 0

        # 2. Unpark. Explicit column list so we don't touch anything we shouldn't.
        now = datetime.now(timezone.utc)
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        # Re-assert the SELECT predicate on UPDATE: without it a row claimed by the
        # drain worker between scan and update would get its ROWVERSION bumped again
        # here, silently voiding that worker's CompleteQboOutbox and stranding the row
        # in_progress.
        attempts_set = ",\n                    Attempts = 0" if args.reset_attempts else ""
        cur.execute(
            f"""
            UPDATE qbo.Outbox
            SET NextRetryAt = ?{attempts_set},
                ModifiedDatetime = ?
            WHERE Id IN ({placeholders})
              AND Status = 'failed'
              AND NextRetryAt > SYSUTCDATETIME()
              AND LastError LIKE ?
            """,
            now,
            now,
            *ids,
            PARKED_LAST_ERROR_PREFIX,
        )
        affected = cur.rowcount
        conn.commit()
        if affected != len(ids):
            skipped = len(ids) - affected
            print(
                f"WARNING: matched {len(ids)} row(s) but updated {affected} — "
                f"{skipped} row(s) changed state between the scan and the update "
                f"(most likely claimed by the drain worker); re-run to catch any "
                f"still-parked rows."
            )
        reset_note = " (Attempts reset to 0)" if args.reset_attempts else ""
        print(
            f"Unparked {affected} row(s){reset_note}. "
            f"Worker will pick them up within ~5s."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
