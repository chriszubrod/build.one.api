# U-362c — Invoice source-linked line recognition: collision tie-break + content-match + DROP gating (DESIGN)

**Status:** `/em` Gate-2 APPROVED 2026-09-03 (both §6 decisions approved by Chris: tie-break-by-content+position; DROP gated on 34070/OHR2-37). Build dispatches as U-362c.
**Class:** P0-surface, money-primary; recognition-algorithm change → design-gated (3 iterative rounds
converged on this root; U-362 shipped-but-halted, U-362b partial-fix-halted).
**Origin:** /em Gate-2 adversarial workflows on U-362 + U-362b (money double-count on invoice draws).
U-362b HALTED: its LinkedTxn recognition **refuses** on the multi-line-same-LinkedTxn collision and
falls through to a Manual-only re-adopt that mints a phantom duplicate — the same money class it exists
to close. Prod (2026-09-03): **1,354 `(InvoiceId, LinkedTxnType, LinkedTxnId)` groups share ≥2
source-linked lines — 28,979 lines** (LinkedTxnId is the source TRANSACTION id, no TxnLineId), so the
refusal path is the COMMON case. Design the recognition to pick the right sibling, not refuse.

---

## 1. Why refusal is wrong (the root)

`_recognize_source_linked_line` keys on `(InvoiceId, LinkedTxnType, LinkedTxnId)` via the U-272
provenance mirror. QBO's `LinkedTxn.TxnId` is the SOURCE TRANSACTION (a Bill/Expense/BillCredit), and
`InvoiceLineItemSourceProvenance` stores no per-line `TxnLineId` — so every sibling invoice line drawn
from ONE multi-line source shares the SAME key. `read_by_linked_txn`'s `len(rows) > 1 → return None`
then drops to the Manual-only `find_stale_identity_orphan` pool (which excludes source-linked lines),
and `_create_line` mints a phantom Manual duplicate → invoice-draw double-count. Refusing on the
collision converts a hard problem into a guaranteed duplicate.

## 2. The fix — tie-break by content + position (mirror the Manual split-line pairing)

On a MISS for a QBO invoice line carrying a LinkedTxn:
1. Read ALL provenance rows matching `(InvoiceId, LinkedTxnType, LinkedTxnId)` (the sibling set) — the
   repo returns the set, not None-on-collision.
2. TIE-BREAK to the incoming QBO line's sibling by CONTENT + POSITION — the same rule
   `find_stale_identity_orphan` already uses for Manual split-lines: filter to siblings whose content
   fingerprint (QboAmount + QboDescription [+ ServiceDate]) matches the incoming line, then pick by
   stable `LineNum`/`.id` order so N incoming lines pair 1:1 with N siblings. The provenance carries
   `QboAmount`, `QboDescription`, `ServiceDate`, `LineNum` — everything the tie-break needs.
3. **Content-match is REQUIRED** (fixes the U-362b "content-blind rebind" P2): never bind a QBO line to
   a sibling whose amount/description doesn't match — that would overwrite the wrong line's amount and
   reset+un-bill its true source (worse than a duplicate). No content-match → fall through to create.
4. Keep the over-recognize / theft guard: never re-adopt a sibling whose CURRENT `qbo_id` is still in
   `live_qbo_line_ids` (it's bound to a different live line). Normalize id types (U-361c).
5. On a genuine no-match (no LinkedTxn, or no content-matching sibling), fall through to the Manual-only
   fingerprint re-adopt exactly as today.

## 3. The 34070 / OHR2-37 stale-mapping DROP gate (data prerequisite)

24 realm invoices share a DocNumber with a second QBO invoice (booked 23cfbfd0); OHR2-37 (dbo.Invoice
1080) has 15 lines whose retired mapping points at the SUPERSEDED QBO invoice. 14 have a correct
`dbo.QboId` (direct HIT, self-heal — safe to drop). **Id 34070 is unstamped and its only identity
linkage runs through the mapping being dropped**, and its provenance was backfilled from the STALE
mapping — so post-DROP its next re-pull could MISS the recognizer and mint a duplicate. **Gate the DROP
(and ideally the deploy) on resolving 34070:** repair its `dbo.QboId` (or its provenance LinkedTxn)
against the LIVE invoice line BEFORE the mapping is gone, or explicitly quarantine it. The DROP runbook
gains a verify: 0 unstamped-but-mapped rows whose linkage is not otherwise recoverable.

## 4. Also re-remove the temporarily-restored audit surface

U-362b restored `backfill_qbo_identity_lines.py`'s `invoice_line_item` entry + an `identity_drift`
registry row + 2 audit scripts to run the backfill on pre-U-362 code. Once the backfill has run (DONE,
69/70), those must be re-removed before the DROP or the audit scripts error against the dropped table.

## 5. Testing (money-primary — the whole point)

- **The collision regression (the 28,979-line case):** an invoice with ≥2 source-linked, unstamped
  siblings sharing one LinkedTxn, different amounts → each incoming QBO line recognizes its OWN sibling
  by content, ONE dbo line each, re-stamped in place, NO phantom Manual, NO draw double-count.
- Same-amount siblings → position (LineNum) pairs them 1:1, money-neutral either way.
- Content-mismatch → no wrong rebind (falls through, doesn't overwrite the wrong line).
- Keep every U-362b test green (unique-LinkedTxn recognition, never-rollback, theft-guard, type-safety).
- **Mutation-prove** the tie-break (neuter the content/position pick → duplicate or wrong-bind returns).
- Full suite at a CLEAN checkout. Own adversarial /code-review pass (money-primary; the last two rounds
  each found a real money bug).

## 6. Decisions for `/em` (Gate-2 of this design)

1. **Approve tie-break-by-content+position** (mirroring `find_stale_identity_orphan`) vs. any alternative
   (e.g. adopting source-linked lines into the fingerprint pool directly) — recommend tie-break: it reuses
   the proven pairing rule and keeps the source-linked/Manual separation the theft-guard needs.
2. **DROP gating on 34070/OHR2-37** — approve as a hard pre-DROP data gate (repair or quarantine), not a
   silent drop.
3. **Dispatch:** approve → U-362c BUILD as a separate prompt; /em runs the revised runbook
   (verify 34070 → apply sprocs → deploy → DROP) after re-Gate-2. Builds AFTER U-363/U-364 or in parallel
   (disjoint from bill/expense line families; shares only base/identity_fastpath.py's matcher, read-only).
