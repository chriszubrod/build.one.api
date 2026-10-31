"""
U-535b b5: the review sample and the sample-gated bulk approval of attachment-backfill candidates.

SAFE BY DEFAULT: every writing action is a dry run unless --apply. Output is counts and ids only: no file names, no
document text. Runs as the CLI system caller (system_authz); writes are attributed to user 17 (the system-caller
convention) unless --as-user-id is given.

Actions (exactly one, with --run <run PublicId> unless noted):
  --draw-sample [--size 300] [--apply]
      Draw a random sample from the run's population: clean, unlinked, bill exists, not claimed by another run, never
      bulk-approved (any human review status), ordered by SHA-256(seed + PublicId). The seed is ALWAYS server-generated
      (no caller can choose it) and stored, so the draw can be reproduced. The population is stored too: only it can
      ever be bulk-approved. Refused after any earlier sample of the run showed a rejection (no sample shopping).
  --status
      Second-review progress (members, missing/approve/reject). The human side (pending/approved/rejected,
      disagreements) is shown only once every member has a second verdict -- the second review is blind.
  --export-sample <dir>
      READ-ONLY. Downloads each member's PDF from Box by its stored file id (25 MB cap, %PDF- check, SHA-256 compared
      with the checked hash; a mismatch is marked and warned) into <dir>/<candidate PublicId>.pdf and writes
      <dir>/manifest.json. The manifest carries NO human review decision (the second review is blind). <dir> must be
      empty (or absent), not inside any git checkout and not under a directory named build.one.
  --record-second-review <csv> [--reviewer NAME] [--apply]
      CSV columns candidate_public_id, verdict (approve|reject), reason (required for reject, <= 200 UTF-16 units).
      Every row is validated (members of the open sample only, no duplicates), then the WHOLE file is written in one
      all-or-nothing transaction. Once every member has a verdict, the verdicts are FROZEN (no further write).
      The reviewer is a free-text label (the CLI runs as the system caller).
  --close-sample [--apply]
      Close the run's open sample. A new one can be drawn only if the closed one was COMPLETE (every human decision and
      second verdict in) and showed no rejection, ever (rejections are sticky); the dry run warns when it is incomplete.
      Note: a member whose bill is deleted after the draw can be neither approved nor closed without blocking redraws,
      so that run can no longer be bulk-approved -- review the rest individually or generate a new run.
  --bulk-approve [--batch 500] [--apply]
      The gate: every sampled candidate approved by the human AND 'approve' from the second reviewer. The dry run prints
      the gate result and what would be approved / excluded. --apply loops batches (each its own transaction, each
      re-checking the gate under lock; a deadlocked batch is retried once) and prints the BulkApproval PublicId.
      Avoid reviewing on the web page while a bulk apply runs.
  --undo-bulk <BulkApproval PublicId> [--apply]   (no --run)
      Return that bulk approval's unlinked candidates to pending; linked ones are kept (undo their links with
      scripts/attachment_backfill_apply.py --undo first).

Exit codes: 0 done; 1 failed (DB/IO error; type name only); 2 unknown run / run not 'ready' / bad arguments
(e.g. export dir inside the repo or not empty); 3 refused by the database (the refusal code is printed);
4 CSV validation failed (nothing written) or the export finished with per-member errors or content_changed;
130 Ctrl-C.

Usage (from the repo root):
  ./.venv/bin/python scripts/attachment_backfill_bulk.py --run <id> --draw-sample [--size 300] [--apply]
  ./.venv/bin/python scripts/attachment_backfill_bulk.py --run <id> --status
  ./.venv/bin/python scripts/attachment_backfill_bulk.py --run <id> --export-sample ~/Downloads/u535b5-sample
  ./.venv/bin/python scripts/attachment_backfill_bulk.py --run <id> --record-second-review verdicts.csv [--apply]
  ./.venv/bin/python scripts/attachment_backfill_bulk.py --run <id> --bulk-approve [--batch 500] [--apply]
  ./.venv/bin/python scripts/attachment_backfill_bulk.py --undo-bulk <bulk id> [--apply]
"""
from __future__ import annotations

import argparse
import signal
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from entities.attachment_backfill.business.bulk import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    DEFAULT_SAMPLE_SIZE,
    AttachmentBackfillBulkService,
    SecondReviewCsvError,
    export_dir_refusal,
    utf16_units,
    parse_second_review_csv,
)
from scripts.sync_helper import assert_cli_system_admin  # noqa: E402
from shared.authz.context import system_authz  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_INVALID = 4
EXIT_INTERRUPTED = 130
SYSTEM_USER_ID = 17


class StopFlag:
    """First Ctrl-C: finish the current item, then stop. Second Ctrl-C: KeyboardInterrupt at once."""

    def __init__(self):
        self.requested = False

    def __call__(self) -> bool:
        return self.requested

    def handle(self, signum, frame) -> None:
        if self.requested:
            raise KeyboardInterrupt
        self.requested = True
        print("\nCtrl-C: finishing the current item, then stopping (Ctrl-C again to abort now).", flush=True)


def _print_counts(label: str, counts: dict) -> None:
    print(f"  {label}:")
    for key in sorted(counts):
        print(f"    {key}: {counts[key]}")


def _default_fetch_factory():
    """(fetch_bytes, close) on one Box client; downloads by the stored file id only."""
    from integrations.box.base.client import BoxHttpClient

    client = BoxHttpClient()

    def fetch(file_id: str) -> bytes:
        return client.download_file(file_id, operation_name="box.attachment_backfill.sample_export")

    return fetch, client.close


def _refused(code: str) -> int:
    print(f"REFUSED: {code}. Nothing written.")
    return EXIT_REFUSED


def _draw(args, service, run_id: int, user_id: int) -> int:
    out = service.draw(run_id, size=args.size, user_id=user_id, apply=args.apply)
    print(f"  size: {args.size}")
    print(f"  eligible_clean: {out.get('EligibleCount')}")
    if out.get("RefusedReason"):
        return _refused(out["RefusedReason"])
    if not args.apply:
        print(f"DRY RUN: would draw {args.size} of {out.get('EligibleCount')} candidates. Re-run with --apply "
              "(the seed is generated by the server at the draw).")
        return EXIT_OK
    print(f"  seed: {out.get('Seed')} (server-generated)")
    print(f"  drawn: {out.get('Drawn')}")
    print(f"  sample: {out.get('SamplePublicId')}")
    return EXIT_OK


STATUS_KEYS = (
    ("Status", "status"), ("SampleSize", "sample_size"), ("Seed", "seed"), ("Members", "members"),
    ("SecondMissing", "second_missing"), ("SecondApprove", "second_approve"), ("SecondReject", "second_reject"),
)
# The human side: printed only once every member has a second verdict (the proc also returns NULL until then).
HUMAN_KEYS = (
    ("HumanPending", "human_pending"), ("HumanApproved", "human_approved"), ("HumanRejected", "human_rejected"),
    ("Disagreements", "disagreements"), ("Linked", "linked"), ("StickyRejections", "sticky_rejections"),
)
BULK_KEYS = (
    ("BulkApprovalPublicId", "bulk_approval"), ("BulkApprovedCount", "bulk_approved_count"),
    ("BulkPartiallyUndone", "bulk_partially_undone"),
)


def _status(service, run_id: int) -> int:
    row = service.status(run_id)
    if not row:
        print("  no sample has been drawn for this run.")
        return EXIT_OK
    print(f"  sample: {row.get('SamplePublicId')}")
    for key, label in STATUS_KEYS:
        print(f"  {label}: {row.get(key)}")
    if row.get("SecondComplete"):
        for key, label in HUMAN_KEYS + BULK_KEYS:
            print(f"  {label}: {row.get(key)}")
        members = row.get("Members") or 0
        both_approved = row.get("HumanApproved") == members and row.get("SecondApprove") == members
        # Advisory only: the SQL gate (BulkApproveAttachmentBackfillCandidates) is authoritative and re-checks everything
        # under lock on every batch; this line just tells the operator an existing bulk may deserve an undo.
        if row.get("BulkApprovalPublicId") and (not both_approved or (row.get("StickyRejections") or 0) > 0):
            print(f"  WARN: gate no longer holds; consider --undo-bulk {row['BulkApprovalPublicId']}")
    else:
        print("  human review: hidden until the second review is complete (blind second review).")
    return EXIT_OK


def _export(args, service, run_id: int, make_fetch: Optional[Callable[[], tuple]]) -> int:
    out_dir = Path(args.export_sample).expanduser()
    refusal = export_dir_refusal(out_dir)
    if refusal:
        print(f"REFUSED: the export directory is {refusal.replace('_', ' ')}. Choose a directory outside it.")
        return EXIT_USAGE
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        print("REFUSED: the export directory exists and is not empty. Choose a new directory.")
        return EXIT_USAGE
    rows = service.members(run_id)
    if not rows:
        print("REFUSED: the run has no open sample.")
        return EXIT_REFUSED
    fetch, close = (make_fetch or _default_fetch_factory)()
    service.fetch_bytes = fetch
    counts: Counter = Counter()
    try:
        stop = service.export_sample(run_id, out_dir, counts=counts, rows=rows)
    finally:
        _print_counts("export", counts)
        try:
            close()
        except Exception:
            pass
    print(f"  wrote: {out_dir} (manifest.json + one PDF per fetched member). Read-only: nothing written to the database.")
    if counts.get("content_changed"):
        print(f"WARN: {counts['content_changed']} member(s) content_changed: the Box file differs from what was checked "
              "(see content_matches_check in the manifest).")
    if stop == "interrupted":
        return EXIT_INTERRUPTED
    return EXIT_INVALID if counts.get("content_changed") or any(k.startswith("error_") for k in counts) else EXIT_OK


def _record(args, service, run_id: int) -> int:
    status = service.status(run_id)
    if not status or status.get("Status") != "open":
        return _refused("no_open_sample")
    member_ids = {str(m["CandidatePublicId"]) for m in service.members(run_id)}
    try:
        text = Path(args.record_second_review).expanduser().read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"FAILED: cannot read the CSV ({type(exc).__name__}).")
        return EXIT_USAGE
    try:
        rows = parse_second_review_csv(text, member_ids)
    except SecondReviewCsvError as exc:
        print(f"INVALID CSV: {len(exc.errors)} bad row(s); nothing written.")
        for line_no, code in exc.errors[:50]:
            print(f"    line {line_no}: {code}")
        return EXIT_INVALID
    verdicts = Counter(r.verdict for r in rows)
    print(f"  rows: {len(rows)} (approve {verdicts['approve']}, reject {verdicts['reject']}); "
          f"members without a row in this file: {len(member_ids) - len(rows)}")
    if not args.apply:
        print("DRY RUN: every row is valid; nothing written. Re-run with --apply.")
        return EXIT_OK
    out = service.record_second_reviews(str(status["SamplePublicId"]), rows, reviewer=args.reviewer)
    if out.get("RefusedReason"):
        row = f" (CSV data row {out['BadRow']})" if out.get("BadRow") else ""
        print(f"REFUSED: {out['RefusedReason']}{row}. Nothing written (the file is all or nothing).")
        return EXIT_REFUSED
    print(f"  recorded: {out.get('Recorded')}")
    print(f"  second_missing: {out.get('SecondMissing')}")
    if out.get("SecondMissing") == 0:
        print("  second review COMPLETE: the verdicts are now frozen.")
    return EXIT_OK


def _close(args, service, run_id: int) -> int:
    status = service.status(run_id)
    if not status or status.get("Status") != "open":
        return _refused("no_open_sample")
    incomplete = (status.get("SecondMissing") or 0) > 0 or (status.get("HumanPending") or 0) > 0
    if incomplete:
        print("  WARN: the sample is incomplete (a human decision or a second verdict is missing). Closing it now "
              "blocks any new draw on this run (prior_sample_incomplete).")
    if not args.apply:
        print(f"DRY RUN: would close sample {status.get('SamplePublicId')}. Re-run with --apply.")
        return EXIT_OK
    out = service.close(run_id)
    if out.get("RefusedReason"):
        return _refused(out["RefusedReason"])
    print(f"  closed: {out.get('SamplePublicId')}")
    return EXIT_OK


def _print_bulk_plan(out: dict) -> bool:
    """Print the dry-run plan; False when the gate is not passed. Until the second review is complete only a generic
    line leaves the CLI (the human side stays hidden)."""
    print(f"  sample_members: {out.get('SampleMembers')}")
    if out.get("RefusedReason") == "second_review_incomplete":
        print("  gate: not_passed (second review incomplete)")
        return False
    print(f"  gate: {out.get('RefusedReason') or 'passed'}")
    print(f"  would_approve: {out.get('Remaining')}")
    print(f"  excluded_bill_missing: {out.get('ExcludedBillMissing')}")
    print(f"  excluded_claimed_elsewhere: {out.get('ExcludedClaimedElsewhere')}")
    return not out.get("RefusedReason")


def _bulk(args, service, run_id: int, user_id: int) -> int:
    plan = service.plan_bulk(run_id, batch_size=args.batch)
    if not _print_bulk_plan(plan):
        if plan.get("RefusedReason") == "second_review_incomplete":
            print("REFUSED: the gate is not passed. Nothing written.")
            return EXIT_REFUSED
        return _refused(plan["RefusedReason"])
    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply.")
        return EXIT_OK
    counts: Counter = Counter()
    bulk_id = refused = stop = None
    try:
        bulk_id, refused, stop = service.bulk_approve(run_id, user_id=user_id, batch_size=args.batch, counts=counts)
    finally:
        _print_counts("bulk approved", counts)
        print(f"  bulk_approval: {bulk_id}")
    if refused:
        if bulk_id:
            print(f"REFUSED mid-way: {refused}. Batches already committed stay approved; --undo-bulk {bulk_id} reverts them.")
        else:
            print(f"REFUSED: {refused}. Nothing written.")
        return EXIT_REFUSED
    if stop == "interrupted":
        print("Interrupted: committed batches stay approved. Re-run to continue (it continues the same bulk approval).")
        return EXIT_INTERRUPTED
    return EXIT_OK


def _undo(args, service, user_id: int) -> int:
    out = service.undo_bulk(args.undo_bulk, user_id=user_id, apply=args.apply)
    if out.get("RefusedReason"):
        return _refused(out["RefusedReason"])
    label = "would_revert" if not args.apply else "reverted"
    print(f"  {label}: {out.get('Reverted')}")
    print(f"  kept_linked: {out.get('KeptLinked')}")
    print(f"  completed: {bool(out.get('Completed'))}")
    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply.")
    elif out.get("KeptLinked"):
        print("  note: linked candidates were kept; undo their links (attachment_backfill_apply.py --undo), then re-run.")
    return EXIT_OK


def run(args, service: AttachmentBackfillBulkService, *, make_fetch: Optional[Callable[[], tuple]] = None) -> int:
    user_id = args.as_user_id
    if args.undo_bulk:
        print(f"\n=== {'APPLY' if args.apply else 'DRY-RUN'}: undo bulk approval {args.undo_bulk} ===")
        return _undo(args, service, user_id)
    run_row = service.repo.read_run_by_public_id(args.run)
    if run_row is None:
        print(f"No run with public id {args.run}.")
        return EXIT_USAGE
    run_id = int(run_row["Id"])
    read_only = args.status or args.export_sample
    mode = "READ" if read_only else ("APPLY" if args.apply else "DRY-RUN")
    print(f"\n=== {mode}: attachment backfill sample run={args.run} status={run_row['Status']} ===")
    if args.status:
        return _status(service, run_id)
    if args.export_sample:
        return _export(args, service, run_id, make_fetch)
    if args.record_second_review:
        return _record(args, service, run_id)
    if args.close_sample:
        return _close(args, service, run_id)
    if run_row["Status"] != "ready":
        print(f"Run is '{run_row['Status']}': only a 'ready' run can be sampled or bulk-approved. Nothing done.")
        return EXIT_USAGE
    if args.draw_sample:
        return _draw(args, service, run_id, user_id)
    return _bulk(args, service, run_id, user_id)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")
    if number < 1:
        raise argparse.ArgumentTypeError(f"{value} must be >= 1")
    return number


def _public_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a PublicId")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="U-535b b5: review sample + sample-gated bulk approval (dry-run by default).")
    ap.add_argument("--run", type=_public_id, help="Run PublicId (every action except --undo-bulk).")
    actions = ap.add_mutually_exclusive_group(required=True)
    actions.add_argument("--draw-sample", action="store_true", help="Draw the run's review sample.")
    actions.add_argument("--status", action="store_true", help="Print the sample's counts.")
    actions.add_argument("--export-sample", metavar="DIR", help="Read-only blind export of the open sample.")
    actions.add_argument("--record-second-review", metavar="CSV", help="Record the second reviewer's verdicts.")
    actions.add_argument("--close-sample", action="store_true", help="Close the run's open sample.")
    actions.add_argument("--bulk-approve", action="store_true", help="Bulk-approve the remaining clean candidates.")
    actions.add_argument("--undo-bulk", metavar="BULK_ID", type=_public_id, help="Undo one bulk approval.")
    ap.add_argument("--apply", action="store_true", help="Write (default: dry run).")
    ap.add_argument("--size", type=_positive_int, default=DEFAULT_SAMPLE_SIZE, help="Sample size (default 300).")
    ap.add_argument("--batch", type=_positive_int, default=DEFAULT_BATCH_SIZE, help="Bulk batch size (default 500).")
    ap.add_argument("--reviewer", default="claude-em", help="Second reviewer label (<= 50 chars).")
    ap.add_argument("--as-user-id", type=_positive_int, default=SYSTEM_USER_ID,
                    help="User id recorded as drawer / approver / undoer (default 17).")
    return ap


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.undo_bulk is None and args.run is None:
        ap.error("--run is required (except with --undo-bulk)")
    if not 1 <= utf16_units(args.reviewer.strip()) <= 50:
        ap.error("--reviewer must be 1..50 characters (UTF-16 units, as NVARCHAR(50) counts them)")
    args.reviewer = args.reviewer.strip()
    return args


def main(argv: Optional[list[str]] = None, *, service: Optional[AttachmentBackfillBulkService] = None,
         stop: Optional[StopFlag] = None, **factories) -> int:
    args = parse_args(argv)
    stop = stop or StopFlag()
    previous = signal.signal(signal.SIGINT, stop.handle)
    try:
        with system_authz():
            service = service or AttachmentBackfillBulkService()
            service.stop_requested = stop
            try:
                return run(args, service, **factories)
            except KeyboardInterrupt:
                print("Aborted (second Ctrl-C). Committed work is kept.")
                return EXIT_INTERRUPTED
            except Exception as exc:
                # Type name only: a driver message could carry SQL or data.
                print(f"FAILED: {type(exc).__name__}.")
                return EXIT_FAILED
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
