"""
Reset dead-lettered rows in `[box].[Outbox]` back to `pending` so the worker
re-attempts them.

Operator use cases:
  - A transient Box outage (5xx / 429 storm) dead-lettered a batch of rows;
    the underlying cause is now resolved and you want another attempt
    without manually editing rows.
  - The service account lost visibility on a folder (visibility-lost
    circuit fired — BoxNotFoundError / BoxPermissionError); the folder
    collaboration has been restored and the rows should retry.
  - A code fix makes a previously non-retryable error retryable; re-run
    the dead-letters with the new code path.

Safety:
  - Dry-run by default. Pass --apply to actually mutate rows.
  - Supports --kind to filter (e.g., only upload_box_file kinds after a
    folder-visibility incident).
  - Resets Attempts=0, NextRetryAt=now, Status='pending', LastError=NULL.
    Preserves RequestId (so the idempotency key dedups any half-completed
    work on the Box side).

Usage:
  python scripts/retry_box_outbox_dead_letters.py                    # dry-run, all dead-letters
  python scripts/retry_box_outbox_dead_letters.py --apply            # actually reset, all
  python scripts/retry_box_outbox_dead_letters.py --kind upload_box_file --apply
  python scripts/retry_box_outbox_dead_letters.py --kind upload_box_file --kind update_box_excel --apply
"""
# Python Standard Library Imports
import sys
from datetime import datetime, timezone

# Local Imports — path dance so the script can be run from the repo root.
sys.path.insert(0, ".")
from scripts.outbox_replay_common import build_replay_parser, preview_dead_letters
from shared.database import get_connection


def main() -> int:
    parser = build_replay_parser(
        description="Reset box.Outbox dead-letter rows back to pending.",
        kind_help=(
            "Only reset rows with the given Kind. Can be repeated. "
            "Example: --kind upload_box_file"
        ),
    )
    args = parser.parse_args()

    with get_connection() as conn:
        cur = conn.cursor()

        rows, exit_code = preview_dead_letters(
            cur,
            schema_table="box.Outbox",
            limit=args.limit,
            kinds=args.kind,
            apply=args.apply,
        )
        if rows is None:
            return exit_code

        now = datetime.now(timezone.utc)
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        cur.execute(
            f"""
            UPDATE box.Outbox
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
        print(f"Reset {len(ids)} row(s) to Status='pending'. Worker will pick them up on the next drain tick.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
