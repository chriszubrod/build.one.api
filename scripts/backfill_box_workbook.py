"""
One-shot backfill: seed a project's Box budget-tracker workbook with DETAILS
rows for every completed Bill, Expense, and BillCredit on that project.

Why this exists
---------------
The Box Excel sync (Phase 3) writes a DETAILS row when a Bill/Expense/BillCredit
COMPLETES with `ALLOW_BOX_WRITES=true`. Anything finalized before Box went live
(2026-06-16) — or before the gate was flipped on for a given environment —
never produced a Box DETAILS row, so the workbook is missing rows for
historical line items. Invoice DRAW-REQUEST stamps key off col-Z (the source
line-item public_id), so a missing source row means the stamp has nothing to
land on. This backfill closes that gap on a per-project basis.

Idempotent: the drain handler dedups by col-Z, so re-running is safe — it just
re-evaluates and writes nothing if every row is already present.

Usage
-----
  # Dry-run — list the entities that WOULD be enqueued.
  python scripts/backfill_box_workbook.py --project <project_public_id>

  # Apply — enqueue one batch outbox row per project (the drain handler
  # downloads the .xlsx once, applies all rows, uploads one new version).
  ALLOW_BOX_WRITES=true python scripts/backfill_box_workbook.py \
      --project <project_public_id> --apply

  # Only specific entity kinds (e.g. just bills):
  python scripts/backfill_box_workbook.py --project <pid> --kind bill --apply

  # All-projects sweep (one batch row per mapped project — careful, this is
  # a lot of work; expect long drain time):
  python scripts/backfill_box_workbook.py --all-projects --apply

Known limitations (out of scope for this script — track separately):
  - Re-stamping historical invoices: this script seeds bill/expense/credit
    DETAILS rows but does NOT re-fire `enqueue_box_excel_draw_stamp` for
    invoices that completed BEFORE the source rows existed. Those invoices
    have empty column H on the Box workbook; a future tool needs to walk
    `[dbo].[Invoice] WHERE IsDraft=0` and re-enqueue stamps after backfill.
  - Pre-existing batch outbox rows write EntityType='box_excel_batch' into
    `[box].[File]` (synthetic row identity); the registry won't surface the
    workbook from a real entity lookup until a single-entity push touches it.
    Pre-existing in the QBO pull, just amplified per project here.
"""
# Python Standard Library Imports
import argparse
import sys

# Local Imports — path dance so the script can be run from the repo root.
sys.path.insert(0, ".")
from shared.authz import set_authz_context
from shared.database import get_connection


VALID_KINDS = ("bill", "expense", "bill_credit")


def _enumerate_project_entities(cur, project_id: int, kinds: tuple) -> dict:
    """
    Return {"bill": [public_ids], "expense": [...], "bill_credit": [...]} for
    every COMPLETED (IsDraft=0) parent that has at least one line item on the
    project. Bill/Expense/BillCredit don't carry ProjectId directly — a single
    parent can span multiple projects via its line items, so we resolve by
    EXISTS over the matching {Bill|Expense|BillCredit}LineItem table.
    """
    out: dict = {}
    if "bill" in kinds:
        cur.execute(
            """SELECT DISTINCT b.PublicId
               FROM dbo.Bill b
               WHERE b.IsDraft = 0
                 AND EXISTS (
                   SELECT 1 FROM dbo.BillLineItem bli
                   WHERE bli.BillId = b.Id AND bli.ProjectId = ?
                 )
               ORDER BY b.PublicId""",
            project_id,
        )
        out["bill"] = [str(r.PublicId) for r in cur.fetchall()]
    if "expense" in kinds:
        cur.execute(
            """SELECT DISTINCT e.PublicId
               FROM dbo.Expense e
               WHERE e.IsDraft = 0
                 AND EXISTS (
                   SELECT 1 FROM dbo.ExpenseLineItem eli
                   WHERE eli.ExpenseId = e.Id AND eli.ProjectId = ?
                 )
               ORDER BY e.PublicId""",
            project_id,
        )
        out["expense"] = [str(r.PublicId) for r in cur.fetchall()]
    if "bill_credit" in kinds:
        cur.execute(
            """SELECT DISTINCT bc.PublicId
               FROM dbo.BillCredit bc
               WHERE bc.IsDraft = 0
                 AND EXISTS (
                   SELECT 1 FROM dbo.BillCreditLineItem bcli
                   WHERE bcli.BillCreditId = bc.Id AND bcli.ProjectId = ?
                 )
               ORDER BY bc.PublicId""",
            project_id,
        )
        out["bill_credit"] = [str(r.PublicId) for r in cur.fetchall()]
    return out


def _resolve_projects(cur, project_public_id: str, all_projects: bool) -> list:
    """
    Resolve the target list: a single project_public_id, OR every project that
    has a Box workbook mapping. Returns list of (id, public_id, name) tuples.
    """
    if all_projects:
        cur.execute(
            """SELECT p.Id, p.PublicId, p.Name
               FROM dbo.Project p
               WHERE EXISTS (SELECT 1 FROM box.ProjectWorkbook bw WHERE bw.ProjectId = p.Id)
               ORDER BY p.Name"""
        )
        return [(r.Id, str(r.PublicId), r.Name) for r in cur.fetchall()]
    cur.execute(
        "SELECT Id, PublicId, Name FROM dbo.Project WHERE PublicId = ?",
        project_public_id,
    )
    rows = cur.fetchall()
    return [(r.Id, str(r.PublicId), r.Name) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a project's Box budget workbook with DETAILS rows for every "
            "completed Bill/Expense/BillCredit on that project."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--project",
        help="Project PublicId (UNIQUEIDENTIFIER) to backfill.",
    )
    group.add_argument(
        "--all-projects",
        action="store_true",
        help="Backfill every project that has a Box workbook mapping.",
    )
    parser.add_argument(
        "--kind",
        action="append",
        default=[],
        choices=VALID_KINDS,
        help="Filter to specific source kinds. Repeatable. Default: all three.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually enqueue. Without this flag the script is read-only.",
    )
    args = parser.parse_args()

    kinds = tuple(args.kind) if args.kind else VALID_KINDS
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)

    # Lazy imports — keep import cost low for dry-run / help.
    from integrations.box.excel.business.mapping_service import (
        BoxProjectWorkbookService,
    )
    from integrations.box.outbox.business.service import BoxOutboxService

    workbook_service = BoxProjectWorkbookService()
    outbox_service = BoxOutboxService()

    with get_connection() as conn:
        cur = conn.cursor()
        projects = _resolve_projects(cur, args.project, args.all_projects)
        if not projects:
            if args.all_projects:
                print(
                    "No projects with a Box workbook mapping ([box].[ProjectWorkbook] is empty?)."
                )
            else:
                print(f"No project found with PublicId {args.project!r}.")
            return 1

        print(f"Resolved {len(projects)} project(s). Kinds: {kinds}")
        print()

        total_enqueued_rows = 0
        total_skipped_unmapped = 0
        total_skipped_empty = 0

        for project_id, project_public_id, project_name in projects:
            mapping = workbook_service.read_by_project_id(project_id)
            if not mapping:
                print(
                    f"  [SKIP] project_id={project_id} ({project_name!r}) — no Box workbook mapping"
                )
                total_skipped_unmapped += 1
                continue

            entity_groups = _enumerate_project_entities(cur, project_id, kinds)
            entities = []
            for kind, pids in entity_groups.items():
                for pid in pids:
                    entities.append({"entity_type": kind, "entity_public_id": pid})

            if not entities:
                print(
                    f"  [SKIP] project_id={project_id} ({project_name!r}) — no completed entities of requested kinds"
                )
                total_skipped_empty += 1
                continue

            summary = ", ".join(f"{k}={len(v)}" for k, v in entity_groups.items())
            print(
                f"  project_id={project_id:>4} {project_name!r:<55} {len(entities):>4} entities ({summary})"
            )

            if not args.apply:
                continue

            row = outbox_service.enqueue_box_excel_batch(
                entities=entities,
                project_id=project_id,
                box_file_id=mapping["box_file_id"],
                worksheet_name=mapping["worksheet_name"],
            )
            if row is None:
                print(
                    "    [REFUSED] enqueue returned None — likely ALLOW_BOX_WRITES is not 'true' "
                    "or the gate refused. No row created."
                )
                continue
            print(f"    [ENQUEUED] outbox row id={row.id} public_id={row.public_id}")
            total_enqueued_rows += 1

        print()
        print(
            f"Done. enqueued={total_enqueued_rows}  unmapped_projects={total_skipped_unmapped}  "
            f"empty_projects={total_skipped_empty}"
        )
        if not args.apply:
            print("DRY-RUN — re-run with --apply (and ALLOW_BOX_WRITES=true) to enqueue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
