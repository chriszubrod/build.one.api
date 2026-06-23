"""
Targeted, staged backfill of QBO bills that are present in QBO but not yet projected
into the local app (no qbo.BillBill mapping).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY (SELECTs only)
and reports exactly which bills fall into which bucket. --apply runs the SAME pipeline a
normal incremental pull uses (connector -> Bill+lines, attachments, then per-project
budget-tracker Excel + SharePoint + Box), scoped to the selected creatable bills.

Buckets (per unmapped qbo.Bill):
  genuinely_missing_creatable -> --apply CREATES (full pipeline; fires Excel/SharePoint/Box)
  already_exists_unlinked     -> skipped (a dbo.Bill already matches vendor+number+date;
                                 the connector would raise "already exists"). Not created.
  unmapped_vendor             -> skipped (QBO vendor not mapped locally)
  null_docnumber              -> skipped by default (no DocNumber -> connector dup-guard can't
                                 protect; needs manual review). Include with --include-null-docnumber.

Usage:
  # dry-run, everything (read-only)
  PYTHONPATH=. python scripts/backfill_qbo_bills.py
  # dry-run, one year
  PYTHONPATH=. python scripts/backfill_qbo_bills.py --year 2023
  # APPLY a small validation batch (5 creatable bills)
  PYTHONPATH=. python scripts/backfill_qbo_bills.py --apply --limit 5
  # APPLY one specific bill by QBO id
  PYTHONPATH=. python scripts/backfill_qbo_bills.py --apply --qbo-id 12345
"""
import argparse
import logging
import sys

from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection, with_retry
from integrations.intuit.qbo.base.pull_race import read_lines_riding_out_race, header_has_amount

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_qbo_bills")

CREATABLE = "genuinely_missing_creatable"


def select_unmapped(*, year=None, qbo_id=None):
    """READ-ONLY: classify every unmapped qbo.Bill into a bucket. Returns list of dicts."""
    where = ["NOT EXISTS (SELECT 1 FROM qbo.BillBill bb WHERE bb.QboBillId = qb.Id)"]
    params = []
    if year:
        where.append("LEFT(qb.TxnDate,4) = ?")
        params.append(str(year))
    if qbo_id:
        where.append("qb.QboId = ?")
        params.append(str(qbo_id))
    sql = f"""
    WITH unmapped AS (
        SELECT qb.Id, qb.QboId, qb.VendorRefValue, qb.VendorRefName, qb.DocNumber, qb.TxnDate
        FROM qbo.Bill qb
        WHERE {' AND '.join(where)}
    ),
    resolved AS (
        SELECT u.*, vv.VendorId AS MappedVendorId
        FROM unmapped u
        LEFT JOIN qbo.Vendor qv       ON qv.QboId = u.VendorRefValue
        LEFT JOIN qbo.VendorVendor vv ON vv.QboVendorId = qv.Id
    )
    SELECT r.Id, r.QboId, r.VendorRefName, r.DocNumber, r.TxnDate, r.MappedVendorId,
      CASE
        WHEN r.MappedVendorId IS NULL THEN 'unmapped_vendor'
        WHEN r.DocNumber IS NULL OR LTRIM(RTRIM(r.DocNumber)) = '' THEN 'null_docnumber'
        WHEN EXISTS (SELECT 1 FROM dbo.Bill b
                     WHERE b.VendorId = r.MappedVendorId
                       AND b.BillNumber = r.DocNumber
                       AND CAST(b.BillDate AS DATE) = TRY_CAST(LEFT(r.TxnDate,10) AS DATE))
          THEN 'already_exists_unlinked'
        ELSE 'genuinely_missing_creatable'
      END AS bucket
    FROM resolved r
    ORDER BY r.TxnDate, r.QboId
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def summarize(rows):
    counts = {}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    return counts


def print_dry_run(rows, limit, include_null):
    counts = summarize(rows)
    print("\n=== DRY RUN (read-only — nothing written) ===")
    print(f"Selected {len(rows)} unmapped qbo.Bill row(s). Buckets:")
    for b in ("genuinely_missing_creatable", "already_exists_unlinked", "unmapped_vendor", "null_docnumber"):
        if b in counts:
            print(f"  {b:30} {counts[b]}")
    creatable = [r for r in rows if r["bucket"] == CREATABLE]
    if include_null:
        creatable += [r for r in rows if r["bucket"] == "null_docnumber"]
    to_process = creatable[: limit] if limit else creatable
    print(f"\n--apply WOULD process {len(to_process)} bill(s)"
          + (f" (capped by --limit {limit} of {len(creatable)} eligible)" if limit and len(creatable) > limit else "")
          + ":")
    for r in to_process:
        print(f"  qbo_id={r['QboId']:>8}  {str(r['TxnDate'])[:10]:10}  {r['VendorRefName'] or '?':30.30}  doc={r['DocNumber'] or '(none)'}  [{r['bucket']}]")
    print("\nRe-run with --apply to project these. (dry-run made no changes.)")


def apply_backfill(rows, limit, include_null):
    """Project the selected creatable bills through the full pull pipeline."""
    # Lazy imports — only when actually applying.
    from integrations.intuit.qbo.bill.business.service import QboBillService
    from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
    from integrations.intuit.qbo.attachable.business.service import QboAttachableService
    from integrations.intuit.qbo.auth.business.service import QboAuthService
    from entities.bill.business.service import BillService
    from entities.bill_line_item.business.service import BillLineItemService
    from scripts.sync_qbo_bill import _link_attachments_to_bill_line_items
    import os as _os

    eligible = [r for r in rows if r["bucket"] == CREATABLE]
    if include_null:
        eligible += [r for r in rows if r["bucket"] == "null_docnumber"]
    to_process = eligible[: limit] if limit else eligible
    if not to_process:
        print("Nothing eligible to apply.")
        return

    qbo_bill_service = QboBillService()
    qbo_bill_repo = QboBillRepository()
    bill_connector = BillBillConnector()
    attachable_service = QboAttachableService()
    bill_service = BillService()
    bill_line_item_service = BillLineItemService()

    auths = QboAuthService().read_all()
    if not auths:
        raise ValueError("No QBO authentication found. Connect QuickBooks first.")
    realm_id = auths[0].realm_id
    print(f"\n=== APPLY: projecting {len(to_process)} bill(s) | realm={realm_id} ===")

    created, attach_synced, failed, skipped, deferred = 0, 0, 0, 0, 0
    failed_ids, skipped_ids, deferred_ids = [], [], []
    synced_bills = []   # (bill_module, bill_id)

    for r in to_process:
        qid = r["QboId"]
        try:
            qbo_bill = qbo_bill_repo.read_by_id(r["Id"])
            if not qbo_bill:
                print(f"  SKIP qbo_id={qid}: staged QboBill {r['Id']} not found")
                continue
            # Re-read lines to ride out the QBO pull-race (an empty read colliding with a
            # non-zero header — lines not yet committed by a concurrent scheduler pull). If they
            # never arrive, DEFER (skip; lands on a later run) rather than letting the connector
            # raise. Shared helper keeps the threshold/logic in lockstep with the connectors —
            # see integrations/intuit/qbo/base/pull_race.py.
            lines = read_lines_riding_out_race(
                qbo_bill_service.read_lines_by_qbo_bill_id, qbo_bill.id, qbo_bill.total_amt,
                attempts=5,
            )
            if not lines and header_has_amount(qbo_bill.total_amt):
                deferred += 1
                deferred_ids.append(qid)
                print(f"  DEFER qbo_id={qid}: no lines yet for non-zero header (pull race) — will land on a later run")
                continue
            # Retry transient DB/connection blips, matching sync_qbo_bill.py (the connector
            # is idempotent on re-run: existing mapping -> UPDATE path, lines re-project).
            bill_module = with_retry(
                bill_connector.sync_from_qbo_bill, qbo_bill, lines,
                max_retries=3, initial_delay=2.0,
            )
            created += 1
            synced_bills.append((bill_module, bill_module.id))
            print(f"  CREATED qbo_id={qid} -> Bill {bill_module.id}")
            # Step 3: attachments
            try:
                atts = attachable_service.sync_attachables_for_bill(
                    realm_id=realm_id, bill_qbo_id=qbo_bill.qbo_id, sync_to_modules=True,
                )
                if atts:
                    _link_attachments_to_bill_line_items(bill_id=bill_module.id, qbo_attachables=atts)
                    attach_synced += len(atts)
            except Exception as att_e:
                logger.warning(f"  attachments failed for qbo_id={qid}: {att_e}")
        except ValueError as ve:
            # Permanent (already-exists / unmapped vendor) — skip, never duplicate.
            skipped += 1
            skipped_ids.append(qid)
            print(f"  SKIP qbo_id={qid} (permanent): {ve}")
        except Exception as e:
            failed += 1
            failed_ids.append(qid)
            logger.exception(f"  FAILED qbo_id={qid}")

    # --- after-loop: per-project budget-tracker Excel + SharePoint + Box (mirrors sync_qbo_bill.py) ---
    excel_rows = sharepoint = box_batches = 0
    if synced_bills:
        project_bill_map, bill_line_counts = {}, {}
        for bill, bill_id in synced_bills:
            try:
                blis = bill_line_item_service.read_by_bill_id(bill_id=bill_id)
                bill_line_counts[bill.id] = len(blis)
                by_project = {}
                for bli in blis:
                    if bli.project_id:
                        by_project.setdefault(bli.project_id, []).append(bli)
                for proj_id, items in by_project.items():
                    project_bill_map.setdefault(proj_id, []).append((bill, items))
            except Exception as e:
                logger.warning(f"  line-item read failed for Bill {bill_id}: {e}")

        for proj_id, pairs in project_bill_map.items():
            try:
                res = bill_service.sync_bills_batch_to_excel(bill_line_pairs=pairs, project_id=proj_id)
                excel_rows += res.get("synced_count", 0)
            except Exception as e:
                logger.warning(f"  Excel sync failed project {proj_id}: {e}")
            for bill, items in pairs:
                try:
                    res = bill_service._upload_attachments_to_module_folder(
                        bill=bill, line_items=items, project_id=proj_id,
                        bill_line_items_count=bill_line_counts.get(bill.id, len(items)),
                    )
                    sharepoint += res.get("synced_count", 0)
                except Exception as e:
                    logger.warning(f"  SharePoint failed project {proj_id}: {e}")

        if _os.getenv("ALLOW_BOX_WRITES", "").strip().lower() == "true":
            from integrations.box.outbox.business.service import BoxOutboxService
            from integrations.box.excel.business.mapping_service import BoxProjectWorkbookService
            box_outbox, box_wb = BoxOutboxService(), BoxProjectWorkbookService()
            for proj_id, pairs in project_bill_map.items():
                for bill, items in pairs:
                    try:
                        bill_service._enqueue_box_uploads(bill, items)
                    except Exception as e:
                        logger.warning(f"  Box doc-push failed project {proj_id}: {e}")
                try:
                    m = box_wb.read_by_project_id(proj_id)
                    if m:
                        ents = [{"entity_type": "bill", "entity_public_id": str(b.public_id)} for b, _ in pairs]
                        if box_outbox.enqueue_box_excel_batch(entities=ents, project_id=proj_id,
                                                              box_file_id=m["box_file_id"], worksheet_name=m["worksheet_name"]):
                            box_batches += 1
                except Exception as e:
                    logger.warning(f"  Box Excel failed project {proj_id}: {e}")

    print("\n=== APPLY SUMMARY ===")
    print(f"  bills created:         {created}")
    print(f"  attachments synced:    {attach_synced}")
    print(f"  excel rows queued:     {excel_rows}")
    print(f"  sharepoint queued:     {sharepoint}")
    print(f"  box excel batches:     {box_batches}")
    print(f"  skipped (permanent):   {skipped} {skipped_ids if skipped_ids else ''}")
    print(f"  deferred (pull race):  {deferred} {deferred_ids if deferred_ids else ''}")
    print(f"  failed:                {failed} {failed_ids if failed_ids else ''}")


def main():
    ap = argparse.ArgumentParser(description="Targeted QBO bill backfill (dry-run by default).")
    ap.add_argument("--apply", action="store_true", help="Actually project (default: dry-run, read-only).")
    ap.add_argument("--year", type=str, default=None, help="Filter by TxnDate year, e.g. 2023.")
    ap.add_argument("--qbo-id", type=str, default=None, help="Backfill one specific QBO bill id.")
    ap.add_argument("--limit", type=int, default=None, help="Max creatable bills to process.")
    ap.add_argument("--include-null-docnumber", action="store_true",
                    help="Also process unmapped bills with no DocNumber (dup-guard can't protect — review first).")
    args = ap.parse_args()

    assert_cli_system_admin()  # system intent for the per-row access guards
    rows = select_unmapped(year=args.year, qbo_id=args.qbo_id)
    if not rows:
        print("No unmapped qbo.Bill rows match the filter.")
        return

    if not args.apply:
        print_dry_run(rows, args.limit, args.include_null_docnumber)
    else:
        apply_backfill(rows, args.limit, args.include_null_docnumber)


if __name__ == "__main__":
    main()
