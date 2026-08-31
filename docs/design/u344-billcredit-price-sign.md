# U-344 — store signed `Price` for BillCredit-sourced invoice line items (DESIGN)

**Status:** Phase-1 design, awaiting `/em` Gate-2. Approval dispatches the build (U-344) + the /em-applied backfill.
**Class:** correctness (money sign) + a coordinated write-site / read-side / backfill change → **design-gated**.
**Origin:** TODO.md 1749 ("Fix `InvoiceInvoiceConnector` storing `Price = abs(Amount)` on BillCreditLineItem-sourced ILIs"). Investigated 2026-08-31 — **genuinely open, but the title is misattributed** (it's the local completion path, not the QBO pull connector).

---

## 1. Problem (verified in-code)

A BillCredit-sourced `dbo.InvoiceLineItem` stores **positive** `Price` (and `Amount`), even though a credit reduces the draw and should be negative. The QBO pull connector is NOT the write site — it only ever writes `SourceType='Manual'` lines. The real write path is **local invoice completion**:
- picker: `entities/invoice/business/service.py:1409` (ready) / `:1499` (draft) — `"price": float(row.BillableAmount)`, `"amount": float(row.Amount)`, both read **positive** from `dbo.BillCreditLineItem` (its `Amount`/`BillableAmount` are stored positive by design — `entities/bill_credit/business/complete_service.py:786-791`).
- flows unchanged through `entities/invoice_line_item/api/router.py:23-38` → `entities/invoice_line_item/business/service.py:64-77` (`repo.create(price=…)`). No sign logic anywhere.

The positive stored value is currently masked at **read time** by three independent negations (all load-bearing today):
1. `entities/invoice/business/cover.py:42-74` `_signed_line_amount` — negates **when `v > 0`** for `source_type == "BillCreditLineItem"`; used by the packet cover rollup (`cover.py:101`).
2. `entities/invoice/api/router.py:29-40` `_toc_signed_amount` — delegates to `_signed_line_amount`; used across the TOC (`router.py:240,303,315,408,420,432`).
3. The draw-matrix sproc `ComputeInvoiceDrawMatrix` (in `entities/invoice/sql/dbo.invoice.sql`) — read-side negation (SESSION_NOTES.md:576).

**Why fix the root:** three hand-maintained read-side negations are fragile — any new consumer of `InvoiceLineItem.Price` that forgets to negate renders a credit as a positive charge (inflating the draw/TOC total, as on BR-MAIN-24: a $21K credit rendered +$21K, a $42K swing). Storing the correct sign once removes the whole class.

## 2. The safety property that makes this SEQUENCEABLE (not atomic)

The naive fear: "flip the write site and every read-side negation must flip in the same deploy or signs invert." **But `_signed_line_amount` only negates when `v > 0`** — a stored **negative** passes through **unchanged**. So the read-side negation is **idempotent with respect to sign**: it displays the correct negative whether the stored value is +X (negate→ −X) or −X (pass through → −X). That means a **mixed-sign state is display-correct**, which lets us sequence safely instead of coordinating one atomic deploy.

⚠️ **Build-unit VERIFICATION POINT (Gate-1 of the build):** confirm ALL THREE read-side negations share this "only-negate-if-positive" shape. `_signed_line_amount`/`_toc_signed_amount` are confirmed. **The `ComputeInvoiceDrawMatrix` sproc negation must be read and confirmed idempotent** (only negates a positive). If any read-side site *unconditionally* negates (`-Price`), that site would flip a stored-negative to positive → its update MUST be co-deployed with the write-site fix (Phase A), not deferred. The design below assumes all three are idempotent; the build confirms and adjusts if not.

## 3. Proposed write-site fix

Negate at the single creation chokepoint — `InvoiceLineItemService.create` (`entities/invoice_line_item/business/service.py:64-77`) — keyed on source type, **idempotent** (store the negative magnitude regardless of input sign):

```python
# BillCredit reduces the draw: store Price/Amount signed-negative at the source
# of truth, so no read-side consumer has to remember to negate (U-344).
if source_type == "BillCreditLineItem":
    price  = -abs(price)  if price  is not None else price
    amount = -abs(amount) if amount is not None else amount
```

`-abs(...)` (not `-price`) makes it idempotent — safe even if a caller ever passes an already-negative value, and safe to run over the backfill's already-negated rows. Only BillCredit is touched; BillLineItem/ExpenseLineItem debits stay positive. `create` is the right place: the QBO pull never makes BillCredit ILIs, so the completion path is the sole producer, and centralizing here catches any future completion path too.

## 4. Migration sequence (safe, ordered — enabled by §2)

- **Phase A — write-site fix (this build, U-344).** New BillCredit ILIs stored negative. Read-side negations stay (idempotent → display stays correct for both new-negative and old-positive rows). Deploy independently. **No coordination needed.**
- **Phase B — backfill (/em-applied after Phase A ships).** Negate existing positive rows. **Corrected predicate** (the TODO:1752 predicate `AND Amount < 0` matches ZERO rows — the ILI's own Amount is stored positive too):
  ```sql
  -- verify first: SELECT COUNT(*), MIN(Price), MAX(Price) FROM dbo.InvoiceLineItem
  --   WHERE SourceType='BillCreditLineItem' AND Price > 0;  -- expect the full population
  UPDATE dbo.InvoiceLineItem
     SET Price = -ABS(Price), Amount = -ABS(Amount)
   WHERE SourceType = 'BillCreditLineItem' AND (Price > 0 OR Amount > 0);
  ```
  Confirm there is no legitimate positive-credit case before running (a credit is always a reduction). Idempotent (`-ABS`), transaction + row-count verify, /em-applied.
- **Phase C — read-side cleanup (optional, later unit).** Once every BillCredit `Price` is negative (A shipped + B applied), the three read-side negations are redundant no-ops and can be simplified/removed — but ONLY after B is confirmed, and each read site updated together. **Recommend deferring C to its own unit** (it's cleanup, not correctness, and removing a safety net prematurely is the actual risk). Book it; don't bundle.

## 5. Blast radius / testing (U-344 = Phase A only)

- Files: `entities/invoice_line_item/business/service.py` (the negation) + a test. **No SQL, no read-side change in this unit** (Phase B backfill is /em SQL; Phase C is a later unit).
- Tests: creating a BillCredit-sourced ILI stores negative `Price`/`Amount`; a BillLineItem/ExpenseLineItem-sourced ILI stays positive; idempotent (`-abs` on an already-negative input stays negative). **Mutation-proof** the negation (remove it → the credit-stays-positive assertion goes RED).
- Because read-side negation is idempotent, existing packet/TOC/draw-matrix output is unchanged for both pre- and post-fix rows during the A→B window (verify with a characterization test on `_signed_line_amount` if cheap).

## 6. Decisions for /em (Gate-2)

1. **Approve the sequence** (A build now, B backfill after ship, C deferred) vs demand one atomic change. Recommend sequenced — the idempotent read-side makes it low-risk and each phase is independently verifiable.
2. **Confirm the draw-matrix sproc negation is idempotent** (build Gate-1) — the one open assumption.
3. **Backfill authorization** — Phase B is a prod money-field UPDATE; /em applies with transaction + verify after Phase A deploys.

## 7. Dispatch
U-344 = **Phase A only** (write-site negation + test). Composer-writes / Codex-reviews. Pure Python, no SQL. Collision note: does NOT touch `dbo.invoice.sql` (Phase A is service-layer only), so it is disjoint from U-343 (1746, the ProposeInvoiceSourceLinks sproc) and from the base-SQL campaign — safe to run in parallel with those. Phase B backfill + Phase C cleanup are booked as follow-ons.
