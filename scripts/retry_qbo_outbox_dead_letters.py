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
import sys
from datetime import datetime, timezone

# Local Imports — path dance so the script can be run from the repo root.
sys.path.insert(0, ".")
from scripts.outbox_replay_common import (
    build_replay_parser,
    preview_dead_letters,
    validate_replay_limit,
)
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
    parser = build_replay_parser(
        description="Reset qbo.Outbox dead-letter rows back to pending.",
        max_limit=MAX_LIMIT,
        kind_help=(
            "Only reset rows with the given Kind. Can be repeated. "
            "Example: --kind sync_bill_to_qbo"
        ),
    )
    args = parser.parse_args()

    if validate_replay_limit(args.limit, max_limit=MAX_LIMIT):
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

    with get_connection() as conn:
        cur = conn.cursor()

        rows, exit_code = preview_dead_letters(
            cur,
            schema_table="qbo.Outbox",
            limit=args.limit,
            kinds=args.kind,
            apply=args.apply,
            qbo_blind_reset_warning=True,
        )
        if rows is None:
            return exit_code

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
