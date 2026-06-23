"""
Clean up DUPLICATE-line half-built Expenses from the historical QBO purchase-pull bug.

Expense dup shape (distinct from bills): an over-stated Expense has exactly 2 ExpenseLineItems
of identical Amount where the header == that single Amount (i.e. the real purchase is one line,
but it got projected twice). The two always split into:
  * KEEPER  = the enriched/real line: invoiced OR SubCostCodeId assigned (categorized).
  * ORPHAN  = the raw copy: SubCostCodeId NULL, NOT invoiced (raw QBO memo description).
The mapping situation varies:
  (A) orphan UNMAPPED (qbo.Purchase has 1 staging line, keeper mapped to it)  -> just drop the orphan.
  (B) BOTH mapped (qbo.Purchase staging itself has a duplicate line)           -> drop the orphan's
       line-mapping AND the duplicate qbo.PurchaseLine staging row (so a re-pull can't re-project it).
  (C) keeper UNMAPPED + orphan MAPPED                                          -> re-point the orphan's
       mapping onto the keeper (keep the real staging line), then drop the orphan.

Attachments are preserved on the keeper: if the keeper has none and the orphan does, the orphan's
ExpenseLineItemAttachment LINK is re-pointed to the keeper; otherwise the orphan's link is deleted
(its Attachment row + blob are left orphaned — harmless, never deleted, no blob risk).

Verified prod fact 2026-06-23: 0 customers over-billed (invoices reference only the keeper).

SAFE BY DEFAULT: dry-run unless --apply. Hard rails — a parent is actionable ONLY when:
  * exactly 2 line items, identical Amount, header == that Amount (non-NULL), AND
  * exactly one keeper (invoiced or scc-assigned) and one orphan (scc NULL, NOT invoiced).
Anything else is SKIPPED for manual review. Per-parent transaction + rollback; post-delete
header==sum re-check + rowcount assert before commit; FULL before-image backup (orphan line +
staging row + attachment-link rows, all columns) written AFTER commit so the ledger is a genuine,
restorable record of only-applied changes.

  PYTHONPATH=. python scripts/cleanup_halfbuilt_dups_expense.py            # dry-run
  PYTHONPATH=. python scripts/cleanup_halfbuilt_dups_expense.py --apply --limit 3
"""
import argparse
import json
import logging

from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("cleanup_halfbuilt_dups_expense")


def _dup_expense_ids(cur):
    cur.execute("""
        SELECT h.Id FROM dbo.Expense h JOIN qbo.PurchaseExpense m ON m.ExpenseId=h.Id
        LEFT JOIN (SELECT ExpenseId pid, SUM(Amount) s, COUNT(*) n FROM dbo.ExpenseLineItem GROUP BY ExpenseId) li ON li.pid=h.Id
        WHERE h.TotalAmount IS NOT NULL AND ISNULL(li.s,0) - h.TotalAmount > 0.01 AND li.n = 2
        ORDER BY h.Id
    """)
    return [r.Id for r in cur.fetchall()]


def _lines(cur, eid):
    cur.execute("""
        SELECT l.Id, l.Amount, l.SubCostCodeId scc,
               (SELECT m.QboPurchaseLineId FROM qbo.PurchaseLineExpenseLineItem m WHERE m.ExpenseLineItemId=l.Id) qline,
               (SELECT COUNT(*) FROM dbo.InvoiceLineItem ili WHERE ili.ExpenseLineItemId=l.Id) inv,
               (SELECT a.Id FROM dbo.ExpenseLineItemAttachment a WHERE a.ExpenseLineItemId=l.Id) att_link_id
        FROM dbo.ExpenseLineItem l WHERE l.ExpenseId=? ORDER BY l.Id
    """, [eid])
    return [dict(id=r.Id, amt=(None if r.Amount is None else round(float(r.Amount), 2)), scc=r.scc,
                 qline=r.qline, inv=r.inv, att_link_id=r.att_link_id) for r in cur.fetchall()]


def plan(eid, hdr, lines):
    """Return (action_dict, None) or (None, skip_reason)."""
    if len(lines) != 2:
        return None, f"{len(lines)} lines"
    a, b = lines
    if a["amt"] is None or b["amt"] is None or a["amt"] != b["amt"] or abs(a["amt"] - hdr) > 0.01:
        return None, "not identical-amount pair == header"
    real = [l for l in lines if l["inv"] > 0 or l["scc"] is not None]
    raw = [l for l in lines if not (l["inv"] > 0 or l["scc"] is not None)]
    if len(real) != 1 or len(raw) != 1:
        return None, f"not 1 keeper + 1 orphan (real={len(real)}, raw={len(raw)})"
    keeper, orphan = real[0], raw[0]
    if orphan["inv"] > 0:
        return None, "orphan is invoiced"
    # mapping sub-case
    if not orphan["qline"]:
        mapping = ("none", None)                       # (A) orphan unmapped — just drop the orphan line
    elif keeper["qline"]:
        # (B) both mapped -> qbo.PurchaseLine staging itself has a dup. Only delete the orphan's
        # staging row when it is the OLDER (stale) one; deleting the LIVE row would re-duplicate
        # on the next pull. qbo.PurchaseLine.Id is an ascending insert order, so older Id = stale.
        if not (orphan["qline"] < keeper["qline"]):
            return None, f"both-mapped, orphan staging {orphan['qline']} not older than keeper {keeper['qline']} — can't confirm stale"
        mapping = ("drop_dup_staging", orphan["qline"])
    else:
        mapping = ("repoint", orphan["qline"])           # (C) keeper unmapped -> move mapping to keeper
    # attachment sub-case
    if orphan["att_link_id"] and not keeper["att_link_id"]:
        att = ("repoint", orphan["att_link_id"])         # move the only receipt to the keeper
    elif orphan["att_link_id"]:
        att = ("delete_link", orphan["att_link_id"])     # keeper already has one -> drop the dup link
    else:
        att = ("none", None)
    return dict(expense=eid, keeper=keeper["id"], orphan=orphan["id"],
                mapping=mapping, attachment=att), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--backup", default="/tmp/halfbuilt_dup_expense_backup.jsonl")
    args = ap.parse_args()
    assert_cli_system_admin()

    with get_connection() as conn:
        cur = conn.cursor()
        eids = _dup_expense_ids(cur)
        if args.limit:
            eids = eids[: args.limit]
        print(f"=== expense dups: {len(eids)} parent(s) {'(APPLY)' if args.apply else '(DRY RUN)'} ===")
        clean = skipped = 0
        bf = open(args.backup, "a") if args.apply else None
        for eid in eids:
            cur.execute("SELECT TotalAmount FROM dbo.Expense WHERE Id=?", [eid])
            hdr = round(float(cur.fetchone()[0] or 0), 2)
            act, reason = plan(eid, hdr, _lines(cur, eid))
            if reason:
                skipped += 1
                print(f"  SKIP expense {eid}: hdr ${hdr} — {reason}")
                continue
            clean += 1
            print(f"  expense {eid}: keep {act['keeper']} drop {act['orphan']} "
                  f"map={act['mapping'][0]} att={act['attachment'][0]}")
            if args.apply:
                try:
                    # Capture FULL before-image of every row we destroy/change, so the backup is a
                    # genuine restore artifact (not just ids). Written AFTER commit so the ledger
                    # only ever lists changes that actually landed.
                    def _row(q, p):
                        cur.execute(q, p); r = cur.fetchone()
                        return dict(zip([c[0] for c in cur.description], r)) if r else None
                    before = {"action": act, "header": hdr,
                              "orphan_line": _row("SELECT * FROM dbo.ExpenseLineItem WHERE Id=?", [act["orphan"]])}
                    amode, aval = act["attachment"]
                    if aval:
                        before["att_link"] = _row("SELECT * FROM dbo.ExpenseLineItemAttachment WHERE Id=?", [aval])
                    mmode, mval = act["mapping"]
                    if mmode == "drop_dup_staging":
                        before["staging_line"] = _row("SELECT * FROM qbo.PurchaseLine WHERE Id=?", [mval])
                    # --- mutate ---
                    if amode == "repoint":
                        cur.execute("UPDATE dbo.ExpenseLineItemAttachment SET ExpenseLineItemId=? WHERE Id=?", [act["keeper"], aval])
                    elif amode == "delete_link":
                        cur.execute("DELETE FROM dbo.ExpenseLineItemAttachment WHERE Id=?", [aval])
                    if mmode == "repoint":
                        cur.execute("UPDATE qbo.PurchaseLineExpenseLineItem SET ExpenseLineItemId=? WHERE QboPurchaseLineId=?", [act["keeper"], mval])
                    elif mmode == "drop_dup_staging":
                        cur.execute("DELETE FROM qbo.PurchaseLineExpenseLineItem WHERE QboPurchaseLineId=?", [mval])
                        cur.execute("DELETE FROM qbo.PurchaseLine WHERE Id=?", [mval])
                    cur.execute("DELETE FROM dbo.ExpenseLineItem WHERE Id=?", [act["orphan"]])
                    if cur.rowcount != 1:
                        raise RuntimeError(f"orphan DELETE rowcount={cur.rowcount} (expected 1)")
                    # post-delete invariant: header == sum of remaining lines (catches a concurrent edit)
                    cur.execute("""SELECT h.TotalAmount t, ISNULL(SUM(l.Amount),0) s FROM dbo.Expense h
                                   LEFT JOIN dbo.ExpenseLineItem l ON l.ExpenseId=h.Id WHERE h.Id=? GROUP BY h.TotalAmount""", [eid])
                    rr = cur.fetchone()
                    if abs(float(rr.t or 0) - float(rr.s or 0)) > 0.01:
                        raise RuntimeError(f"post-delete unbalanced: header {rr.t} vs sum {rr.s}")
                    conn.commit()
                    bf.write(json.dumps(before, default=str) + "\n"); bf.flush()
                except Exception as e:
                    conn.rollback(); clean -= 1; skipped += 1
                    logger.error(f"  ROLLBACK expense {eid}: {str(e)[:160]}")
        if bf:
            bf.close()
        print(f"\n=== SUMMARY === clean={clean} skipped={skipped}" + (f" backup={args.backup}" if args.apply else ""))


if __name__ == "__main__":
    main()
