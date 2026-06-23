"""
Clean up DUPLICATE-line half-built Bills/Expenses from the historical QBO pull-dup bug:
a re-sync couldn't find the existing app line (no line-mapping), so it created a QBO-MAPPED
duplicate next to the UNMAPPED original. The original is the real line (invoiced and/or
attachment-bearing); the duplicate is QBO-mapped, un-invoiced, attachment-free.

Remediation per fingerprint group: keep the unmapped original, RE-POINT the duplicate's
qbo line-mapping onto it (so a future pull updates in place, not re-duplicates), then delete
the duplicate. Verified prod fact 2026-06-23: 0 customers over-billed — invoices reference
only the keeper, so deleting the un-invoiced duplicate changes no customer invoice.

HARD SAFETY (after adversarial review wf_48d75cc2):
  * Fingerprint = (Amount, Description, Quantity, Rate, SubCostCodeId, ProjectId) — far
    tighter than (amount, desc), so two genuinely-distinct real lines don't collide.
  * NULL-Amount lines are excluded entirely (placeholders, never grouped/deleted).
  * A group is actionable ONLY when it is exactly {1 unmapped keeper + 1 mapped duplicate}.
    Anything else (0/≥2 unmapped, ≥2 mapped, size≠2) is SKIPPED for manual review.
  * The duplicate is deletable ONLY if it has ZERO references from EVERY inbound FK except
    the line-mapping table (discovered live from sys.foreign_keys — catches InvoiceLineItem,
    *Attachment, ContractLabor, ContractLaborLineItem, and anything added later).
  * Apply only when post-delete parent sum == header.
  * Per-parent transaction with rollback-on-error + continue; before-state backup is appended
    and flushed per parent (a mid-run crash stays reversible).

SAFE BY DEFAULT: dry-run unless --apply.
  PYTHONPATH=. python scripts/cleanup_halfbuilt_dups.py                  # dry-run bills
  PYTHONPATH=. python scripts/cleanup_halfbuilt_dups.py --entity expense
  PYTHONPATH=. python scripts/cleanup_halfbuilt_dups.py --apply --limit 3
"""
import argparse
import json
import logging

from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("cleanup_halfbuilt_dups")

FP_COLS = ["Amount", "Description", "Quantity", "Rate", "SubCostCodeId", "ProjectId"]

CFG = {
    "bill": dict(
        hdr="dbo.Bill", line="dbo.BillLineItem", line_fk="BillId",
        linemap="qbo.BillLineItemBillLine", linemap_dbo="BillLineItemId", linemap_qbo="QboBillLineId",
    ),
    "expense": dict(
        hdr="dbo.Expense", line="dbo.ExpenseLineItem", line_fk="ExpenseId",
        linemap="qbo.PurchaseLineExpenseLineItem", linemap_dbo="ExpenseLineItemId", linemap_qbo="QboPurchaseLineId",
    ),
}


def discover_external_fks(cur, line_tbl, exclude_tbl):
    """All inbound FK (schema.table, column) referencing dbo.<line_tbl>, minus the line-map
    table (we repoint that, not block on it). Live from the catalog so new FKs are covered."""
    cur.execute(f"""
        SELECT OBJECT_SCHEMA_NAME(fk.parent_object_id) sch, OBJECT_NAME(fk.parent_object_id) tbl, c.name col
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
        JOIN sys.columns c ON c.object_id=fk.parent_object_id AND c.column_id=fkc.parent_column_id
        WHERE fk.referenced_object_id = OBJECT_ID('dbo.{line_tbl}')
    """)
    out = []
    excl = exclude_tbl.split(".")[-1].lower()
    for r in cur.fetchall():
        if r.tbl.lower() == excl:
            continue
        out.append((f"{r.sch}.{r.tbl}", r.col))
    return out


def _dup_parent_ids(cur, c):
    cur.execute(f"""
        SELECT h.Id FROM {c['hdr']} h
        LEFT JOIN (SELECT {c['line_fk']} pid, SUM(Amount) s FROM {c['line']} GROUP BY {c['line_fk']}) li ON li.pid=h.Id
        WHERE h.TotalAmount IS NOT NULL AND ISNULL(li.s,0) - h.TotalAmount > 0.01
        ORDER BY h.Id
    """)
    return [r.Id for r in cur.fetchall()]


def _lines(cur, c, ext_fks, pid):
    ext_expr = " + ".join(f"(SELECT COUNT(*) FROM {t} WHERE {col}=l.Id)" for t, col in ext_fks) or "0"
    cur.execute(f"""
        SELECT l.Id, l.Amount, ISNULL(l.Description,'') d, l.Quantity, l.Rate, l.SubCostCodeId, l.ProjectId,
               (SELECT m.{c['linemap_qbo']} FROM {c['linemap']} m WHERE m.{c['linemap_dbo']}=l.Id) qbo_line_id,
               ({ext_expr}) ext_refs
        FROM {c['line']} l WHERE l.{c['line_fk']}=? ORDER BY l.Id
    """, [pid])
    out = []
    for r in cur.fetchall():
        out.append(dict(
            id=r.Id, amt=(None if r.Amount is None else round(float(r.Amount), 2)), d=r.d,
            fp=(None if r.Amount is None else (round(float(r.Amount), 2), r.d,
                str(r.Quantity), str(r.Rate), r.SubCostCodeId, r.ProjectId)),
            qbo_line_id=r.qbo_line_id, ext_refs=r.ext_refs))
    return out


def plan_parent(lines):
    """Return (actions, reason). actions=[{delete_line_id, keeper_id, repoint_qbo_line_id}].
    reason is set (skip explanation) when not cleanly actionable."""
    from collections import defaultdict
    groups = defaultdict(list)
    for ln in lines:
        if ln["fp"] is None:   # NULL Amount → never group/delete
            continue
        groups[ln["fp"]].append(ln)
    actions = []
    for fp, grp in groups.items():
        if len(grp) < 2:
            continue
        if len(grp) != 2:
            return [], f"group size {len(grp)} for fp {fp} (manual review)"
        unmapped = [g for g in grp if not g["qbo_line_id"]]
        mapped = [g for g in grp if g["qbo_line_id"]]
        if len(unmapped) != 1 or len(mapped) != 1:
            return [], f"group not {{1 unmapped + 1 mapped}} (unmapped={len(unmapped)}, mapped={len(mapped)})"
        keeper, dup = unmapped[0], mapped[0]
        if dup["ext_refs"] > 0:
            return [], f"duplicate line {dup['id']} has {dup['ext_refs']} external FK ref(s) — not safe"
        actions.append(dict(delete_line_id=dup["id"], keeper_id=keeper["id"], repoint_qbo_line_id=dup["qbo_line_id"]))
    return actions, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", choices=["bill", "expense"], default="bill")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--backup", default="/tmp/halfbuilt_dup_backup.jsonl")
    args = ap.parse_args()
    assert_cli_system_admin()
    c = CFG[args.entity]

    with get_connection() as conn:
        cur = conn.cursor()
        ext_fks = discover_external_fks(cur, c["line"].split(".")[-1], c["linemap"])
        print(f"external FK guards for {args.entity}: {ext_fks}")
        pids = _dup_parent_ids(cur, c)
        if args.limit:
            pids = pids[: args.limit]
        print(f"=== {args.entity}: {len(pids)} DUP parent(s) {'(APPLY)' if args.apply else '(DRY RUN)'} ===")

        clean = skipped = total_del = 0
        bf = open(args.backup, "a") if args.apply else None
        for pid in pids:
            cur.execute(f"SELECT TotalAmount FROM {c['hdr']} WHERE Id=?", [pid])
            hdr = round(float(cur.fetchone()[0] or 0), 2)
            lines = _lines(cur, c, ext_fks, pid)
            actions, reason = plan_parent(lines)
            del_ids = {a["delete_line_id"] for a in actions}
            new_sum = round(sum(l["amt"] for l in lines if l["amt"] is not None and l["id"] not in del_ids), 2)
            if reason or not actions or abs(new_sum - hdr) > 0.01:
                skipped += 1
                print(f"  SKIP {args.entity} {pid}: hdr ${hdr} would_sum ${new_sum} acts={len(actions)} reason={reason or 'unbalanced/none'}")
                continue
            clean += 1
            print(f"  {args.entity} {pid}: hdr ${hdr} delete {len(actions)} dup -> ${new_sum} OK")
            if args.apply:
                try:
                    for a in actions:
                        bf.write(json.dumps(dict(entity=args.entity, parent=pid, **a)) + "\n")
                    bf.flush()
                    for a in actions:
                        # repoint the dup's line-mapping onto the unmapped keeper, then delete the dup line
                        cur.execute(f"UPDATE {c['linemap']} SET {c['linemap_dbo']}=? WHERE {c['linemap_qbo']}=?",
                                    [a["keeper_id"], a["repoint_qbo_line_id"]])
                        cur.execute(f"DELETE FROM {c['line']} WHERE Id=?", [a["delete_line_id"]])
                        total_del += 1
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    total_del -= len(actions)  # this parent rolled back
                    clean -= 1; skipped += 1
                    logger.error(f"  ROLLBACK {args.entity} {pid}: {str(e)[:160]}")
        if bf:
            bf.close()
        print(f"\n=== SUMMARY === clean={clean} skipped={skipped} lines_deleted={total_del}"
              + (f" backup={args.backup}" if args.apply else ""))


if __name__ == "__main__":
    main()
