"""
U-535b b1: generate tier-A Bill -> Box PDF link CANDIDATES and a neutral per-PDF check (status + evidence counts).

Staging only. Writes dbo.AttachmentBackfillRun / dbo.AttachmentBackfillCandidate and nothing
else: no Attachment, BillLineItemAttachment, blob or outbox row. Box and Document Intelligence
are only READ. Every link is approved individually later (b2/b3) and written in b4.

SAFE BY DEFAULT: dry-run unless --apply. Output is counts only; --verbose adds candidate
filenames (local terminal only — never paste it anywhere).

Usage (from the repo root):
  ./.venv/bin/python scripts/attachment_backfill_candidates.py generate --root-folder-id <id> [--apply]
  ./.venv/bin/python scripts/attachment_backfill_candidates.py verify --run <public_id> [--batch 50] [--workers 4] [--apply]
  ./.venv/bin/python scripts/attachment_backfill_candidates.py generate --root-folder-id <id> --resume-run <public_id> --apply
  ./.venv/bin/python scripts/attachment_backfill_candidates.py cancel --run <public_id> [--apply]
  ./.venv/bin/python scripts/attachment_backfill_candidates.py summary --run <public_id>
  (also runnable as: ./.venv/bin/python -m scripts.attachment_backfill_candidates ...)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from entities.attachment_backfill.business.project_map import ProjectRef  # noqa: E402
from entities.attachment_backfill.business.service import AttachmentBackfillService  # noqa: E402
from scripts.sync_helper import assert_cli_system_admin  # noqa: E402
from shared.authz.context import system_authz  # noqa: E402

DEFAULT_COMPANY_ID = 1


def _print_counts(label: str, counts: dict) -> None:
    print(f"  {label}:")
    for key, value in counts.items():
        print(f"    {key}: {value}")


def _load_projects() -> list[ProjectRef]:
    from entities.project.business.service import ProjectService

    return [
        ProjectRef(id=int(p.id), name=p.name, abbreviation=p.abbreviation)
        for p in ProjectService().read_all()
        if p.id is not None
    ]


def _resolve_run(service: AttachmentBackfillService, public_id: str) -> Optional[dict]:
    run = service.read_run(public_id)
    if run is None:
        print(f"No run with public id {public_id}.")
    return run


def _list_box_inventory(root_folder_id: str):
    from entities.attachment_backfill.business.box_inventory import walk_inventory
    from integrations.box.base.client import BoxHttpClient

    with BoxHttpClient() as client:
        return walk_inventory(client, root_folder_id)


def cmd_generate(
    args,
    service: AttachmentBackfillService,
    *,
    list_inventory: Optional[Callable[[str], list]] = None,
    load_projects: Optional[Callable[[], list]] = None,
) -> int:
    run = None
    if args.resume_run:
        run = _resolve_run(service, args.resume_run)
        if run is None:
            return 2
        if run["Status"] != "generating":
            print(f"Run {args.resume_run} is '{run['Status']}', not 'generating': cannot resume. Nothing done.")
            return 2
    elif args.apply and not args.force_new_run:
        open_runs = service.open_runs("box")
        if open_runs:
            existing = open_runs[0]
            print(
                f"Run {existing['PublicId']} (box) is already '{existing['Status']}'. Resume it with --resume-run, "
                "cancel it, or pass --force-new-run. Nothing done."
            )
            return 2

    inventory = (list_inventory or _list_box_inventory)(args.root_folder_id)
    bills = service.repo.read_bills_for_backfill(company_id=args.company_id)
    projects = (load_projects or _load_projects)()
    print(f"\n=== {'APPLY' if args.apply else 'DRY-RUN'}: attachment backfill generate (box) ===")
    print(f"  inventory_pdfs: {len(inventory)}  unlinked_bills: {len(bills)}  projects: {len(projects)}")

    plan = service.plan_box_candidates(inventory, bills, projects)
    _print_counts("plan", plan.counts())
    if args.verbose:
        for c in plan.candidates:
            print(f"    bill={c.bill_id} file={c.file_id} tier={c.tier} {c.file_name}")

    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply to insert candidates.")
        return 0

    if run is None:
        run = service.create_run(source="box", notes=args.notes)
    print(f"  run: {run['PublicId']}")
    # Resume prunes this run's pending, unchecked candidates the new plan no longer proposes (reported as pruned_stale).
    counts = service.generate_box_candidates(
        int(run["Id"]), inventory, bills, projects, prune_stale=bool(args.resume_run)
    )
    _print_counts("inserted", counts)
    updated = service.mark_run_ready(int(run["Id"]))
    print(f"  run status: {updated['Status'] if updated else 'unchanged (status update refused)'}")
    return 0


def cmd_verify(args, service: AttachmentBackfillService) -> int:
    run = _resolve_run(service, args.run)
    if run is None:
        return 2
    if run["Status"] != "ready":
        print(f"Run {args.run} is '{run['Status']}': only a 'ready' run can be verified. Nothing done.")
        return 2
    run_id = int(run["Id"])
    print(f"\n=== {'APPLY' if args.apply else 'DRY-RUN'}: attachment backfill verify run={args.run} ===")
    if not args.apply:
        pending = sum(
            int(r["Unchecked"] or 0) + (int(r["Errored"] or 0) if args.include_errors else 0)
            for r in service.run_summary(run_id)
            if r["ReviewStatus"] == "pending"
        )
        print(f"  would check: {pending} candidate(s) (batch={args.batch}, workers={args.workers})")
        print("DRY RUN: nothing downloaded or written. Re-run with --apply.")
        return 0

    from integrations.box.base.client import BoxHttpClient

    with BoxHttpClient() as client:
        try:
            counts = service.verify_candidates(
                run_id,
                fetch_bytes=lambda file_id: client.download_file(
                    file_id, operation_name="box.attachment_backfill.download"
                ),
                batch=args.batch,
                workers=args.workers,
                include_errors=args.include_errors,
                limit=args.limit,
            )
        except KeyboardInterrupt:
            print("Interrupted: rows already written are kept; the rest stay unchecked. Re-run verify to continue.")
            return 130
    _print_counts("checked", counts)
    return 0


def cmd_cancel(args, service: AttachmentBackfillService) -> int:
    run = _resolve_run(service, args.run)
    if run is None:
        return 2
    print(f"\n=== {'APPLY' if args.apply else 'DRY-RUN'}: cancel run={args.run} (status {run['Status']}) ===")
    if not args.apply:
        print("DRY RUN: nothing changed. Re-run with --apply to cancel.")
        return 0
    updated = service.cancel_run(int(run["Id"]))
    if updated is None:
        print(f"  refused: a '{run['Status']}' run cannot be cancelled.")
        return 2
    print(f"  run status: {updated['Status']}")
    return 0


def cmd_summary(args, service: AttachmentBackfillService) -> int:
    run = _resolve_run(service, args.run)
    if run is None:
        return 2
    print(f"\n=== attachment backfill run={args.run} source={run['Source']} status={run['Status']} ===")
    # Neutral distributions only: the check never presents a conclusion (round 3).
    for r in service.run_summary(int(run["Id"])):
        print(f"  tier={r['MatchTier']} review={r['ReviewStatus']}")
        for key, value in r.items():
            if key not in ("MatchTier", "ReviewStatus"):
                print(f"    {key}: {int(value or 0)}")
    return 0


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")
    if number < 1:
        raise argparse.ArgumentTypeError(f"{value} must be >= 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="U-535b b1: backfill link candidates (dry-run by default).")
    sub = ap.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="List Box invoice PDFs and generate tier-A candidates.")
    gen.add_argument("--root-folder-id", required=True)
    gen.add_argument("--company-id", type=int, default=DEFAULT_COMPANY_ID)
    gen.add_argument("--notes", default=None)
    gen.add_argument("--resume-run", default=None, help="Insert into this existing 'generating' run (public id).")
    gen.add_argument(
        "--force-new-run", action="store_true",
        help="Open a new run even though a 'generating' or 'ready' run of this source exists.",
    )
    gen.add_argument("--apply", action="store_true")
    gen.add_argument("--verbose", action="store_true", help="Also print candidate filenames (local only).")

    ver = sub.add_parser("verify", help="Download each candidate PDF and record its check status + evidence.")
    ver.add_argument("--run", required=True, help="Run public id.")
    ver.add_argument("--batch", type=_positive_int, default=50)
    ver.add_argument("--workers", type=_positive_int, default=4)
    ver.add_argument("--limit", type=_positive_int, default=None, help="Stop after N candidates.")
    ver.add_argument("--include-errors", action="store_true", help="Re-check candidates whose check status is 'error'.")
    ver.add_argument("--apply", action="store_true")

    can = sub.add_parser("cancel", help="Cancel a generating/ready run (never an applied one).")
    can.add_argument("--run", required=True)
    can.add_argument("--apply", action="store_true")

    summ = sub.add_parser("summary", help="Evidence distributions by tier / review status.")
    summ.add_argument("--run", required=True)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    service = AttachmentBackfillService()
    handlers = {"generate": cmd_generate, "verify": cmd_verify, "cancel": cmd_cancel, "summary": cmd_summary}
    with system_authz():
        return handlers[args.command](args, service)


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
