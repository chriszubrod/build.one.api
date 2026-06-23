"""
Heal UNDER-stated half-built Bills/Expenses: header total > sum of dbo line items because
some/all lines never projected (the cross-process pull-race / regen left the record header-only
or short a line, while the lines sit in qbo.*Line staging).

Heal = re-run the connector UPDATE-path, which projects the staging lines into dbo (creating the
missing ones, matching existing via the line-mapping). Purely ADDITIVE — no deletion, no FK risk.

SAFETY:
  * Only heal records where staging Σ == header (so projecting actually balances it) AND either
    the record is header-only (0 dbo lines → no existing line to duplicate) OR every existing dbo
    line is already line-mapped (so the connector matches, never duplicates). Anything else SKIPPED.
  * Post-heal verify: re-read dbo; require header == Σ lines, else report (no auto-rollback — the
    connector commits internally — but the pre-rails make a duplicating heal impossible).
  * Direct connector call → NO Excel/SharePoint/Box side effects (those live in the sync scripts).
  * with_retry for transient DB blips. Audit log of before/after line counts.

SAFE BY DEFAULT: dry-run unless --apply.
  PYTHONPATH=. python scripts/heal_halfbuilt_missing.py --entity bill
  PYTHONPATH=. python scripts/heal_halfbuilt_missing.py --entity expense --apply --limit 5
"""
import argparse
import json
import logging

from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection, with_retry

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("heal_halfbuilt_missing")

CFG = {
    "bill": dict(
        hdr="dbo.Bill", line="dbo.BillLineItem", line_fk="BillId",
        mp="qbo.BillBill", mp_dbo="BillId", mp_qbo="QboBillId",
        qline="qbo.BillLine", qline_fk="QboBillId",
        linemap="qbo.BillLineItemBillLine", linemap_dbo="BillLineItemId",
    ),
    "expense": dict(
        hdr="dbo.Expense", line="dbo.ExpenseLineItem", line_fk="ExpenseId",
        mp="qbo.PurchaseExpense", mp_dbo="ExpenseId", mp_qbo="QboPurchaseId",
        qline="qbo.PurchaseLine", qline_fk="QboPurchaseId",
        linemap="qbo.PurchaseLineExpenseLineItem", linemap_dbo="ExpenseLineItemId",
    ),
}


def _connector(entity):
    if entity == "bill":
        from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
        from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository
        from integrations.intuit.qbo.bill.business.service import QboBillService
        c = BillBillConnector()
        return (lambda qid: QboBillRepository().read_by_id(qid),
                lambda qid: QboBillService().read_lines_by_qbo_bill_id(qid),
                lambda ent, lines: c.sync_from_qbo_bill(ent, lines))
    from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
    from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository
    from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
    c = PurchaseExpenseConnector()
    return (lambda qid: QboPurchaseRepository().read_by_id(qid),
            lambda qid: QboPurchaseService().read_lines_by_qbo_purchase_id(qid),
            lambda ent, lines: c.sync_from_qbo_purchase(ent, lines))


def _targets(cur, c):
    """Missing-line records that are safe to heal: header > Σ dbo, staging count > dbo count,
    staging Σ == header, and (header-only OR every existing dbo line is line-mapped)."""
    cur.execute(f"""
      SELECT h.Id, m.{c['mp_qbo']} qbo_id, h.TotalAmount hdr, ISNULL(li.n,0) dn,
             (SELECT COUNT(*) FROM {c['qline']} q WHERE q.{c['qline_fk']}=m.{c['mp_qbo']}) sn,
             (SELECT ISNULL(SUM(q.Amount),0) FROM {c['qline']} q WHERE q.{c['qline_fk']}=m.{c['mp_qbo']}) ss,
             (SELECT COUNT(*) FROM {c['line']} l WHERE l.{c['line_fk']}=h.Id
                AND NOT EXISTS (SELECT 1 FROM {c['linemap']} mm WHERE mm.{c['linemap_dbo']}=l.Id)) unmapped_existing
      FROM {c['hdr']} h JOIN {c['mp']} m ON m.{c['mp_dbo']}=h.Id
      LEFT JOIN (SELECT {c['line_fk']} pid, COUNT(*) n, SUM(Amount) s FROM {c['line']} GROUP BY {c['line_fk']}) li ON li.pid=h.Id
      WHERE h.TotalAmount IS NOT NULL AND h.TotalAmount - ISNULL(li.s,0) > 0.01
        AND (SELECT COUNT(*) FROM {c['qline']} q WHERE q.{c['qline_fk']}=m.{c['mp_qbo']}) > ISNULL(li.n,0)
      ORDER BY h.Id
    """)
    return cur.fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", choices=["bill", "expense"], default="bill")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--backup", default="/tmp/halfbuilt_missing_heal.jsonl")
    args = ap.parse_args()
    assert_cli_system_admin()
    c = CFG[args.entity]
    read_ent, read_lines, sync = _connector(args.entity) if args.apply else (None, None, None)

    with get_connection() as conn:
        cur = conn.cursor()
        rows = _targets(cur, c)
        if args.limit:
            rows = rows[: args.limit]
        print(f"=== {args.entity}: {len(rows)} missing-line record(s) {'(APPLY)' if args.apply else '(DRY RUN)'} ===")
        healed = skipped = 0
        bf = open(args.backup, "a") if args.apply else None
        for r in rows:
            staging_balances = abs(float(r.ss) - float(r.hdr)) <= 0.01
            safe_shape = (r.dn == 0) or (r.unmapped_existing == 0)
            if not staging_balances or not safe_shape:
                skipped += 1
                print(f"  SKIP {args.entity} {r.Id}: hdr ${r.hdr} dbo {r.dn} staging {r.sn}=${r.ss} "
                      f"(staging_balances={staging_balances}, unmapped_existing={r.unmapped_existing})")
                continue
            print(f"  {args.entity} {r.Id}: hdr ${r.hdr} dbo {r.dn} line(s) -> project {r.sn} staging line(s)")
            if args.apply:
                try:
                    ent = read_ent(r.qbo_id)
                    lines = read_lines(r.qbo_id)
                    if not lines:
                        raise RuntimeError("staging read returned no lines")
                    with_retry(sync, ent, lines, max_retries=4, initial_delay=2.0)
                    cur.execute(f"""SELECT h.TotalAmount t, ISNULL(SUM(l.Amount),0) s, COUNT(l.Id) n FROM {c['hdr']} h
                                    LEFT JOIN {c['line']} l ON l.{c['line_fk']}=h.Id WHERE h.Id=? GROUP BY h.TotalAmount""", [r.Id])
                    pr = cur.fetchone()
                    ok = abs(float(pr.t or 0) - float(pr.s or 0)) <= 0.01
                    bf.write(json.dumps(dict(entity=args.entity, id=r.Id, qbo_id=r.qbo_id,
                             before_lines=r.dn, after_lines=pr.n, header=str(r.hdr), after_sum=str(pr.s), balanced=ok), default=str) + "\n")
                    bf.flush()
                    if ok:
                        healed += 1
                        print(f"      -> {pr.n} line(s)=${pr.s} BALANCED")
                    else:
                        skipped += 1
                        print(f"      -> *** {pr.n} line(s)=${pr.s} vs header ${pr.t} NOT BALANCED (review)")
                except Exception as e:
                    skipped += 1
                    logger.error(f"  FAILED {args.entity} {r.Id}: {str(e)[:160]}")
        if bf:
            bf.close()
        print(f"\n=== SUMMARY === healed={healed} skipped={skipped}" + (f" backup={args.backup}" if args.apply else ""))


if __name__ == "__main__":
    main()
