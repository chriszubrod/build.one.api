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
import sys
from datetime import datetime, timezone

# Local Imports — path dance so the script can be run from the repo root.
sys.path.insert(0, ".")
from scripts.outbox_replay_common import build_replay_parser, preview_dead_letters
from shared.database import get_connection


def main() -> int:
    parser = build_replay_parser(
        description="Reset ms.Outbox dead-letter rows back to pending.",
        kind_help=(
            "Only reset rows with the given Kind. Can be repeated. "
            "Example: --kind upload_sharepoint_file --kind append_excel_row"
        ),
    )
    args = parser.parse_args()

    with get_connection() as conn:
        cur = conn.cursor()

        rows, exit_code = preview_dead_letters(
            cur,
            schema_table="ms.Outbox",
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
