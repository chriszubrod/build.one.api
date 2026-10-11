"""
One-shot backfill: mark one attachment group's PDFs for text extraction (U-535a).

Opt-in and scoped by link table, not category. The scheduled drain only processes
'pending' rows for groups enabled via ATTACHMENT_EXTRACTION_GROUPS; this script
queues a group's existing never-extracted PDFs (and, with --include-failed, its
failed PDFs). Completed rows are never touched.

SAFE BY DEFAULT: dry-run unless --apply is passed. The dry run prints the counts
the sproc would act on; --apply marks rows in batches and prints the counts again.

Usage (from the repo root):
  PYTHONPATH=. ./.venv/bin/python scripts/attachment_extraction_backfill.py --group bill
  PYTHONPATH=. ./.venv/bin/python scripts/attachment_extraction_backfill.py --group w9 --include-failed --apply
"""
from __future__ import annotations

import argparse

from entities.attachment.business.extraction_service import VALID_EXTRACTION_GROUPS
from entities.attachment.persistence.repo import AttachmentRepository
from scripts.sync_helper import assert_cli_system_admin

GROUPS = tuple(sorted(VALID_EXTRACTION_GROUPS))


def _print_counts(label: str, counts: dict) -> None:
    print(
        f"  {label}: group={counts['group']} "
        f"never_extracted_pdfs={counts['never_extracted_pdfs']} "
        f"failed_pdfs={counts['failed_pdfs']} "
        f"already_pending={counts['already_pending']} "
        f"completed={counts['completed']}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mark one attachment group's PDFs pending for text extraction (dry-run by default)."
    )
    ap.add_argument("--group", required=True, choices=GROUPS)
    ap.add_argument(
        "--include-failed",
        action="store_true",
        help=(
            "Also re-mark this group's failed PDFs (clears their ExtractionError). Resets at most "
            "--batch-size failed PDFs per run; re-run to reset more."
        ),
    )
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--apply", action="store_true", help="Write the status change (default: dry-run).")
    args = ap.parse_args(argv)

    repo = AttachmentRepository()
    scope = dict(group=args.group, include_failed=args.include_failed, batch_size=args.batch_size)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: attachment extraction backfill ===")
    _print_counts("before", repo.mark_pending_extraction_by_group(**scope, dry_run=True))

    if not args.apply:
        print("DRY RUN: no rows changed. Re-run with --apply to mark them pending.")
        return 0

    after = repo.mark_pending_extraction_by_group(**scope, dry_run=False)
    print(f"  marked pending: {after['affected']}")
    _print_counts("after", after)
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
