"""
Backfill: mask taxpayer-id shapes in existing EmailAttachment.DiResultJson rows (U-535i).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry run prints how many rows would
change and up to 40 attachment ids — never JSON content.

Usage (from the repo root):
  PYTHONPATH=. ./.venv/bin/python -m scripts.remask_email_di_results
  PYTHONPATH=. ./.venv/bin/python -m scripts.remask_email_di_results --apply
"""
from __future__ import annotations

import argparse

from entities.email_message.persistence.repo import (
    EmailAttachmentRepository,
    mask_di_result_json_for_persistence,
)
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

_BATCH_SIZE = 500


def _fetch_di_result_batch(after_id: int) -> list[tuple[int, str, bytes]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP (?) Id, DiResultJson, RowVersion
            FROM dbo.[EmailAttachment]
            WHERE DiResultJson IS NOT NULL
              AND Id > ?
            ORDER BY Id
            """,
            _BATCH_SIZE,
            after_id,
        )
        rows = [
            (int(row.Id), row.DiResultJson, bytes(row.RowVersion)) for row in cursor.fetchall()
        ]
        conn.rollback()
        return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mask TIN shapes in EmailAttachment DiResultJson (dry-run by default)."
    )
    ap.add_argument("--apply", action="store_true", help="Write masked JSON (default: dry-run).")
    args = ap.parse_args(argv)

    repo = EmailAttachmentRepository() if args.apply else None
    scanned = 0
    pending: list[tuple[int, bytes, str]] = []
    updated = skipped_changed = failed = 0
    last_id = 0

    while True:
        batch = _fetch_di_result_batch(last_id)
        if not batch:
            break
        batch_pending: list[tuple[int, bytes, str]] = []
        for row_id, raw, row_version in batch:
            scanned += 1
            masked = mask_di_result_json_for_persistence(raw)
            if masked != raw:
                entry = (row_id, row_version, masked)
                batch_pending.append(entry)
                pending.append(entry)
        if args.apply and repo is not None:
            for row_id, row_version, masked in batch_pending:
                try:
                    if repo.update_di_result_json(
                        id=row_id,
                        di_result_json=masked,
                        expected_row_version=row_version,
                    ):
                        updated += 1
                    else:
                        skipped_changed += 1
                except Exception:
                    failed += 1
        last_id = batch[-1][0]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: remask email DI results ===")
    print(f"  scanned={scanned} would_change={len(pending)}")
    if pending:
        ids_preview = [row_id for row_id, _, _ in pending[:40]]
        print(f"  ids={ids_preview}")

    if not args.apply:
        print("DRY RUN: no rows changed. Re-run with --apply to write masked JSON.")
        return 0

    print(f"  updated={updated} skipped_changed={skipped_changed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
