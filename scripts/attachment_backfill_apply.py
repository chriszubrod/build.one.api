"""
U-535b b4: link APPROVED backfill candidates to their bills (one BillLineItemAttachment per bill line), or undo a run.

SAFE BY DEFAULT: dry-run unless --apply. A dry run reads the database only -- no Box download, no blob, no write --
and reports what an apply would do plus every refusal the database alone can decide. Output is counts only: no file
names, no document text.

--apply (per approved, unlinked candidate of a 'ready' run; review continues meanwhile):
  - refuses to start when the database is at/over --max-db-percent of its max size (or the size is unknown), and stops
    between candidates when a re-check (every --size-check-every candidates) crosses it;
  - downloads the file by its stored Box id, requires a PDF whose SHA-256 equals the hash the reviewer saw, reuses an
    Attachment with that hash or creates one (blob + row), then links in one atomic transaction;
  - moves the run to 'applied' when nothing in it is left pending or approved-unlinked.
--undo [--apply]: reverses this run's links (only the link rows the apply recorded); an attachment the apply created is
  deleted with its blob when nothing else references it. Review decisions stay 'approved'.

Exit codes: 0 done; 1 the pass failed (DB error; counts so far are printed); 2 unknown run / run not 'ready';
3 database-size refusal or stop; 4 finished but some candidates hit a counted error (error_*, *_kept_error,
close_error, blob_remove_failed); 130 Ctrl-C.

Ctrl-C finishes the current candidate and stops (exit 130); a second Ctrl-C aborts at once (a just-created attachment
whose link did not commit is still cleaned up). No outbox, SharePoint, Box write, Excel, QBO or email.

Usage (from the repo root):
  ./.venv/bin/python scripts/attachment_backfill_apply.py --run <public_id>                    # dry run
  ./.venv/bin/python scripts/attachment_backfill_apply.py --run <public_id> --apply [--limit N] [--max-db-percent 85]
  ./.venv/bin/python scripts/attachment_backfill_apply.py --run <public_id> --undo [--apply]
"""
from __future__ import annotations

import argparse
import signal
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from entities.attachment_backfill.business.apply import (  # noqa: E402
    DEFAULT_MAX_DB_PERCENT,
    DEFAULT_SIZE_CHECK_EVERY,
    STOP_DB_SIZE,
    STOP_INTERRUPTED,
    AttachmentBackfillApplyService,
    DbSize,
)
from scripts.sync_helper import assert_cli_system_admin  # noqa: E402
from shared.authz.context import system_authz  # noqa: E402

EXIT_INTERRUPTED = 130
EXIT_FAILED = 1
EXIT_ERRORS = 4


def _has_errors(counts: dict) -> bool:
    """Any per-candidate error the pass absorbed and counted (it kept going, but a human should look)."""
    return any(
        value and (key.startswith("error_") or key.endswith("_kept_error") or key in ("close_error", "blob_remove_failed"))
        for key, value in counts.items()
    )


class AzureBlobStore:
    """The one place the apply touches blob storage (composition root; the entity package only sees store/remove)."""

    def __init__(self):
        from shared.storage import AzureBlobStorage

        self._storage = AzureBlobStorage()

    def store(self, blob_name: str, content: bytes, content_type: str) -> str:
        return self._storage.upload_file(blob_name=blob_name, file_content=content, content_type=content_type)

    def remove(self, blob_url: str) -> None:
        self._storage.delete_file(blob_url)


class StopFlag:
    """First Ctrl-C: finish the current candidate, then stop. Second Ctrl-C: KeyboardInterrupt at once."""

    def __init__(self):
        self.requested = False

    def __call__(self) -> bool:
        return self.requested

    def handle(self, signum, frame) -> None:  # pragma: no cover - exercised via test calling handle directly
        if self.requested:
            raise KeyboardInterrupt
        self.requested = True
        print("\nCtrl-C: finishing the current candidate, then stopping (Ctrl-C again to abort now).", flush=True)


def _print_counts(label: str, counts: dict) -> None:
    print(f"  {label}:")
    for key in sorted(counts):
        print(f"    {key}: {counts[key]}")


def _fmt_mb(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{value:,.1f}"


def _print_size(size: DbSize, threshold: float) -> None:
    pct = size.percent
    print(
        f"  database: used {_fmt_mb(size.used_mb)} MB / cap {_fmt_mb(size.max_mb)} MB = "
        f"{'unknown' if pct is None else f'{pct:.1f}'}% (threshold {threshold:g}%)"
    )


def _default_fetch_factory():
    """(fetch_bytes, close) on one Box client; downloads by the stored file id only."""
    from integrations.box.base.client import BoxHttpClient

    client = BoxHttpClient()

    def fetch(file_id: str) -> bytes:
        return client.download_file(file_id, operation_name="box.attachment_backfill.apply_download")

    return fetch, client.close


def _run_pass(label: str, fn: Callable[[Counter], tuple]) -> tuple[Optional[str], bool]:
    """(stop_reason, had_errors). The Counter is filled in place and ALWAYS printed (DB error, Ctrl-C, success)."""
    counts: Counter = Counter()
    try:
        stop = fn(counts)[1]
    finally:
        errors = _has_errors(counts)
        _print_counts(label, counts)
        if errors:
            print("  ERRORS: some candidates hit an error (counted above); see the log by candidate/attachment id.")
    return stop, errors


def run(
    args,
    service: AttachmentBackfillApplyService,
    *,
    make_blobs: Optional[Callable[[], object]] = None,
    make_fetch: Optional[Callable[[], tuple]] = None,
) -> int:
    run_row = service.repo.read_run_by_public_id(args.run)
    if run_row is None:
        print(f"No run with public id {args.run}.")
        return 2
    run_id = int(run_row["Id"])
    mode = "APPLY" if args.apply else "DRY-RUN"
    action = "undo" if args.undo else "apply"
    print(f"\n=== {mode}: attachment backfill {action} run={args.run} status={run_row['Status']} ===")

    if args.undo:
        if not args.apply:
            stop, errors = _run_pass("would undo", lambda c: service.plan_undo(run_id, counts=c))
            if stop == STOP_INTERRUPTED:
                print("Interrupted. Nothing changed.")
                return EXIT_INTERRUPTED
            print("DRY RUN: nothing changed. Re-run with --undo --apply to remove this run's links.")
            return EXIT_ERRORS if errors else 0
        service.blobs = (make_blobs or AzureBlobStore)()
        stop, errors = _run_pass("undo", lambda c: service.undo(run_id, limit=args.limit, counts=c))
        if stop == STOP_INTERRUPTED:
            return EXIT_INTERRUPTED
        return EXIT_ERRORS if errors else 0

    if run_row["Status"] != "ready":
        print(f"Run is '{run_row['Status']}': only a 'ready' run can be applied. Nothing done.")
        return 2

    size = service.db_size()
    _print_size(size, args.max_db_percent)
    if not args.apply:
        stop, errors = _run_pass("would apply", lambda c: service.plan(run_id, limit=args.limit, counts=c))
        if stop == STOP_INTERRUPTED:
            print("Interrupted. Nothing downloaded or written.")
            return EXIT_INTERRUPTED
        if not size.allows(args.max_db_percent):
            print("  note: an --apply now would be REFUSED by the database-size threshold.")
        print("DRY RUN: nothing downloaded or written. Re-run with --apply.")
        return EXIT_ERRORS if errors else 0
    if not size.allows(args.max_db_percent):
        print("REFUSED: database size is at/over the threshold (or unknown). Nothing done.")
        return 3

    fetch, close = (make_fetch or _default_fetch_factory)()
    service.blobs = (make_blobs or AzureBlobStore)()
    service.fetch_bytes = fetch
    try:
        stop, errors = _run_pass("applied", lambda c: service.apply(
            run_id,
            limit=args.limit,
            max_db_percent=args.max_db_percent,
            size_check_every=args.size_check_every,
            counts=c,
        ))
    finally:
        try:
            close()
        except Exception:
            pass
    if stop == STOP_DB_SIZE:
        _print_size(service.db_size(), args.max_db_percent)
        print("STOPPED: the database reached the size threshold. Linked rows are kept; the rest stay approved.")
        return 3
    if stop == STOP_INTERRUPTED:
        print("Interrupted: linked rows are kept; the rest stay approved and unlinked. Re-run to continue.")
        return EXIT_INTERRUPTED
    return EXIT_ERRORS if errors else 0


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")
    if number < 1:
        raise argparse.ArgumentTypeError(f"{value} must be >= 1")
    return number


def _percent(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number")
    if not 0 < number <= 100:
        raise argparse.ArgumentTypeError(f"{value} must be in (0, 100]")
    return number


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="U-535b b4: link approved backfill candidates (dry-run by default).")
    ap.add_argument("--run", required=True, help="Run public id.")
    ap.add_argument("--apply", action="store_true", help="Write (default: dry run, DB reads only).")
    ap.add_argument("--undo", action="store_true", help="Reverse this run's links (writes only with --apply).")
    ap.add_argument("--limit", type=_positive_int, default=None, help="Stop after N candidates.")
    ap.add_argument("--max-db-percent", type=_percent, default=DEFAULT_MAX_DB_PERCENT,
                    help="Refuse/stop at this %% of the database max size (default 85).")
    ap.add_argument("--size-check-every", type=_positive_int, default=DEFAULT_SIZE_CHECK_EVERY,
                    help="Re-check the database size every N candidates (default 25).")
    return ap


def main(argv: Optional[list[str]] = None, *, service: Optional[AttachmentBackfillApplyService] = None,
         stop: Optional[StopFlag] = None, **factories) -> int:
    args = build_parser().parse_args(argv)
    stop = stop or StopFlag()
    previous = signal.signal(signal.SIGINT, stop.handle)
    try:
        with system_authz():
            service = service or AttachmentBackfillApplyService()
            service.stop_requested = stop
            try:
                return run(args, service, **factories)
            except KeyboardInterrupt:
                print("Aborted (second Ctrl-C). Committed links are kept; re-run to continue.")
                return EXIT_INTERRUPTED
            except Exception as exc:
                # Type name only: a driver message could carry SQL or data.
                print(f"FAILED: {type(exc).__name__}. Counts above are what was done; re-run to continue.")
                return EXIT_FAILED
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
