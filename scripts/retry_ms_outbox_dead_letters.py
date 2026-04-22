"""
Reset dead-lettered rows in `[ms].[Outbox]` back to `pending` so the worker
re-attempts them.

Operator use cases:
  - A transient Graph outage dead-lettered a batch of rows; the underlying
    cause is now resolved and you want another attempt without manually
    editing rows.
  - A code fix makes a previously non-retryable error retryable; re-run
    the dead-letters with the new code path.
  - An Azure AD permission change unblocked calls; retry everything that
    failed during the permission gap.

Safety:
  - Dry-run by default. Pass --apply to actually mutate rows.
  - Supports --kind to filter (e.g., only excel_row kinds after an Excel
    outage).
  - Resets Attempts=0, NextRetryAt=now, Status='pending', LastError=NULL.
    Preserves RequestId (so Graph dedups any half-completed work).

Usage:
  python scripts/retry_ms_outbox_dead_letters.py                    # dry-run, all dead-letters
  python scripts/retry_ms_outbox_dead_letters.py --apply            # actually reset, all
  python scripts/retry_ms_outbox_dead_letters.py --kind upload_sharepoint_file --apply
  python scripts/retry_ms_outbox_dead_letters.py --kind append_excel_row --kind insert_excel_row --apply
"""
# Python Standard Library Imports
import argparse
import sys
from datetime import datetime, timezone

# Local Imports — path dance so the script can be run from the repo root.
sys.path.insert(0, ".")
from shared.database import get_connection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset ms.Outbox dead-letter rows back to pending."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate rows. Without this flag the script is read-only.",
    )
    parser.add_argument(
        "--kind",
        action="append",
        default=[],
        help="Only reset rows with the given Kind. Can be repeated. "
             "Example: --kind upload_sharepoint_file --kind append_excel_row",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Cap the number of rows reset (default 1000).",
    )
    args = parser.parse_args()

    where_clauses = ["Status = 'dead_letter'"]
    params: list = []
    if args.kind:
        placeholders = ",".join("?" for _ in args.kind)
        where_clauses.append(f"Kind IN ({placeholders})")
        params.extend(args.kind)
    where = " AND ".join(where_clauses)

    with get_connection() as conn:
        cur = conn.cursor()

        # 1. Show what would be affected.
        cur.execute(
            f"""
            SELECT TOP ({args.limit})
                Id, PublicId, Kind, EntityType, EntityPublicId, Attempts,
                CONVERT(VARCHAR(19), DeadLetteredAt, 120) AS DeadLetteredAt,
                LEFT(LastError, 120) AS LastError
            FROM ms.Outbox
            WHERE {where}
            ORDER BY Id
            """,
            *params,
        )
        rows = cur.fetchall()
        if not rows:
            print("No dead-letter rows matched the filter. Nothing to do.")
            return 0

        print(f"Found {len(rows)} dead-letter row(s){' matching filter' if args.kind else ''}:")
        print()
        for r in rows:
            print(
                f"  Id={r[0]:>6}  Kind={r[2]:<25} Entity={r[3]:<15} "
                f"EntityPID={str(r[4])[:8]} Attempts={r[5]} "
                f"DL={r[6]} Err={r[7]}"
            )
        print()

        if not args.apply:
            print("DRY-RUN: no rows modified. Re-run with --apply to reset these rows.")
            return 0

        # 2. Reset. Explicit column list so we don't touch anything we shouldn't.
        now = datetime.now(timezone.utc)
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        cur.execute(
            f"""
            UPDATE ms.Outbox
            SET Status = 'pending',
                Attempts = 0,
                NextRetryAt = ?,
                LastError = NULL,
                DeadLetteredAt = NULL,
                ModifiedDatetime = ?
            WHERE Id IN ({placeholders})
            """,
            now,
            now,
            *ids,
        )
        conn.commit()
        print(f"Reset {len(ids)} row(s) to Status='pending'. Worker will pick them up within ~5s.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
