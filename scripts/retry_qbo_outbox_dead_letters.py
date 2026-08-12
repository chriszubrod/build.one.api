"""
Reset dead-lettered rows in `[qbo].[Outbox]` back to `pending` so the worker
re-attempts them.

Operator use cases:
  - A transient QBO outage dead-lettered a batch of rows; the underlying
    cause is now resolved and you want another attempt without manually
    editing rows.
  - A code fix makes a previously non-retryable error retryable; re-run
    the dead-letters with the new code path.
  - ALLOW_QBO_WRITES was accidentally off and rows dead-lettered after
    exhausting retries.

Safety:
  - Dry-run by default. Pass --apply to actually mutate rows.
  - Supports repeatable --kind to filter (strongly recommended — a QBO
    dead-letter may sit on a partially completed write, unlike write-refused
    where nothing left the process).
  - Resets Status='pending', Attempts=0, NextRetryAt=now. **Preserves
    RequestId** — load-bearing for Intuit requestid dedup. Also preserves
    LastError and DeadLetteredAt as audit history of why the row dead-lettered.
  - UPDATE re-asserts Status='dead_letter' (TOCTOU guard — see
    scripts/unpark_qbo_outbox_budget.py:111-114).

Usage:
  python scripts/retry_qbo_outbox_dead_letters.py --kind sync_bill_to_qbo     # dry-run
  python scripts/retry_qbo_outbox_dead_letters.py --kind sync_bill_to_qbo --apply
  python scripts/retry_qbo_outbox_dead_letters.py --apply --limit 50
"""
# Python Standard Library Imports
import argparse
import sys
from datetime import datetime, timezone

# Local Imports — path dance so the script can be run from the repo root.
sys.path.insert(0, ".")
from shared.database import get_connection

# Must match QboOutboxWorker._dispatch_table keys.
KNOWN_KINDS = frozenset({
    "sync_bill_to_qbo",
    "sync_expense_to_qbo",
    "sync_invoice_to_qbo",
    "recode_purchase_line",
})

# SQL Server caps bound parameters at 2100; UPDATE uses 2 datetime params + N ids.
MAX_LIMIT = 2098


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset qbo.Outbox dead-letter rows back to pending."
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
             "Example: --kind sync_bill_to_qbo",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help=f"Cap the number of rows reset (default 1000, max {MAX_LIMIT}).",
    )
    args = parser.parse_args()

    if args.limit < 1 or args.limit > MAX_LIMIT:
        print(f"Error: --limit must be between 1 and {MAX_LIMIT}.")
        return 2

    if args.kind:
        unknown = sorted(set(args.kind) - KNOWN_KINDS)
        if unknown:
            print(
                f"Error: unknown --kind value(s): {unknown}. "
                f"Known kinds: {sorted(KNOWN_KINDS)}"
            )
            return 2

    where_clauses = ["Status = 'dead_letter'"]
    params: list = []
    if args.kind:
        placeholders = ",".join("?" for _ in args.kind)
        where_clauses.append(f"Kind IN ({placeholders})")
        params.extend(args.kind)
    where = " AND ".join(where_clauses)

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute(
            f"""
            SELECT TOP ({args.limit})
                Id, PublicId, Kind, EntityType, EntityPublicId, Attempts,
                CONVERT(VARCHAR(19), DeadLetteredAt, 120) AS DeadLetteredAt,
                LEFT(LastError, 120) AS LastError
            FROM qbo.Outbox
            WHERE {where}
            ORDER BY Id
            """,
            *params,
        )
        rows = cur.fetchall()
        if not rows:
            print("No dead-letter rows matched the filter. Nothing to do.")
            return 0

        scope = f" matching filter {args.kind!r}" if args.kind else " (ALL kinds)"
        print(f"Found {len(rows)} dead-letter row(s){scope}:")
        if not args.kind:
            print()
            print(
                "WARNING: No --kind filter. A blind full reset can re-issue a write that "
                "already landed in QBO. Prefer --kind sync_bill_to_qbo (or the specific "
                "kind) after confirming LastError and entity state."
            )
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

        now = datetime.now(timezone.utc)
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        cur.execute(
            f"""
            UPDATE qbo.Outbox
            SET Status = 'pending',
                Attempts = 0,
                NextRetryAt = ?,
                ModifiedDatetime = ?
            WHERE Id IN ({placeholders})
              AND Status = 'dead_letter'
            """,
            now,
            now,
            *ids,
        )
        affected = cur.rowcount
        conn.commit()
        if affected != len(ids):
            skipped = len(ids) - affected
            print(
                f"WARNING: matched {len(ids)} row(s) but updated {affected} — "
                f"{skipped} row(s) changed state between the scan and the update "
                f"(most likely claimed by the drain worker); re-run to catch any "
                f"still-dead-lettered rows."
            )
        print(
            f"Reset {affected} row(s) to Status='pending'. "
            f"RequestId, LastError, and DeadLetteredAt preserved. "
            f"Worker will pick them up within ~30s."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
