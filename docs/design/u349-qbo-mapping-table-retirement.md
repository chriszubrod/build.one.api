# U-349 — Retire the QBO mapping-table second-stores, then DROP (DESIGN / SCOPING)

**Status:** design/scoping, awaiting `/em` decision. **This is NOT a DROP script.** The
design-gate census below found the 11 "orphaned" mapping tables are still live-consumed.
**Class:** schema change + reconciliation-query refactor → design-gated, multi-unit.
**Origin:** Chris asked (2026-09-01) why the `qbo.*` staging schema still exists — 31 base
tables — when the trust-dbo migration ("dbo native `QboId` is the SOLE identity store, drop
the redundant `qbo.*` mapping/staging second-stores") should have removed it. Only `qbo.Item`
was ever dropped (U-307d).

---

## 1. The correction — why this is NOT "drop 11 tables now"

A first-pass census counted references to each mapping table's **repository class**
(`QboBillBillRepository`, etc.) and found **0 live-code references** for all 11 → looked
cleanly orphaned. **That signal was misleading.** The write path moved off the repo classes,
but the design-gate raw-SQL census (`FROM/JOIN/INTO/UPDATE/DELETE qbo.<Table>`) found live
consumers the repo-class grep can't see:

- **The reconciliation service still JOINs these mapping tables in LIVE drift methods**
  (`integrations/intuit/qbo/reconciliation/business/service.py`):
  - `reconcile_invoice_draws` → `FROM qbo.InvoiceInvoice map` (line 363)
  - `_reconcile_bill_billable_status_drift` (**U-335**) → `JOIN qbo.BillLineItemBillLine` (line 1430)
  - `_reconcile_purchase_billable_status_drift` (**U-335**) → `JOIN qbo.PurchaseLineExpenseLineItem` (line 1537)
  These run in the **daily reconcile**; U-335 shipped them recently, so they are unambiguously live.
- **Every one of the 11 has a live connector CRUD sproc set** (`INSERT/SELECT/DELETE`) in its
  own `connector/**/sql/qbo.*.sql` — the pull **write path** likely still populates the second-store.
- Two `persistence/repo.py` files (`invoice_invoice`, `invoice_line_item_invoice_line`) carry raw
  `cursor.execute("SELECT ... FROM [qbo].[InvoiceInvoice]")` reads — confirm called-or-dead per unit.

**Conclusion:** the 11 mapping tables are the still-live **fallback / secondary identity store**
(the U-238a/b/c "native `QboId` fast-path, fall back to the mapping-table hop" pattern), NOT orphans.
A DROP now breaks daily reconciliation. Correct signal = the raw-SQL census, re-run to **0 executed
refs** per table, not the repo-class count.

## 2. FK topology (the good news)

Verified against prod `sys.foreign_keys` (2026-09-01):
- **No incoming FKs** reference any of the 11 → nothing external blocks a DROP once the code
  consumers are gone.
- Their only FKs are **outgoing** to their parent staging entity table (`BillBill → Bill`,
  `InvoiceInvoice → Invoice`, `PurchaseExpense → Purchase`, …) → those drop away with the table;
  **drop order among the 11 is unconstrained.**
- Those same outgoing FKs are **why the ~15 raw-staging entity tables can't drop yet** (their
  mapping children pin them). Retiring the 11 mapping tables **partially unblocks** the later
  staging-entity phase — so this is the correct first move regardless.

## 3. Proposed sequence (per entity family, design-gated)

The 11 group into entity families: `bill` (BillBill), `bill_line_item` (BillLineItemBillLine),
`invoice` (InvoiceInvoice), `invoice_line_item` (InvoiceLineItemInvoiceLine),
`purchase/expense` (PurchaseExpense), `purchase_line/expense_line_item` (PurchaseLineExpenseLineItem),
`vendorcredit` (VendorCreditBillCredit), `vendorcredit_line_item` (VendorCreditLineItemBillCreditLineItem),
`physical_address` (PhysicalAddressAddress), `term` (TermPaymentTerm), `company_info` (CompanyInfoCompany).

Per family:
1. **Re-express the reconciliation drift/draw queries dbo-native** — join `dbo.<X>.QboId`/`RealmId`
   instead of the `qbo.<mapping>` hop. Mirrors **U-300a**'s dbo-only conflict-check re-expression.
   Prove equivalence (dbo-QboId join result set == mapping-hop result set) with a characterization
   test before removing the mapping read.
2. **Remove the connector's mapping WRITE** — stop populating the second-store; confirm the U-341
   `create_mapping_then_stamp` path's identity is fully carried by the dbo `QboId` stamp.
3. **Verify** — reconcile runs green dbo-native; pull still resolves identity; **re-run the raw-SQL
   census → 0 executed refs** for that table.
4. **DROP** the mapping table + retire its `connector/**/sql/qbo.*.sql`, `persistence/repo.py`, and
   repo class. `/em`-applied (schema change), transaction + verify.

## 4. Blast radius / risk

- **Reconciliation drift ACCURACY is the risk surface** — each re-expressed query must be proven
  equivalent before its table drops, or drift detection silently changes. This is the whole reason
  it's design-gated, not a DROP.
- **Forward-only, no data migration** — the mapping rows are redundant with `dbo.<X>.QboId`, which
  is already the identity store. Nothing to backfill.
- Non-runtime references (`scripts/analyze_billcredit_attachment_backfill.py`,
  `scripts/migrations/qbo_vendorcredit_mapping_backfill.sql`, `sql/dev/*.samples.sql`) are historical
  — annotate/retire, don't block.

## 5. Sizing / dispatch

- **This is a multi-unit program, not one unit.** Recommend one design-approved unit **per entity
  family** (or per read/write pair), each Map → remove-fallback → verify → drop, sequenced.
- **Start with `company_info`** as the pattern-setter: 1 mapping table, 1 connector, the thinnest
  reconciliation footprint — prove the retire-then-drop shape once, then replicate across the other 10.
- The DROP is the **last step** of each family's unit, `/em`-applied. Builders never drop prod tables.

## 6. Decisions for /em

1. **Approve the retire-then-drop program** (per-family, reconciliation re-expressed dbo-native
   first) vs. leave the mapping tables in place as a working (if redundant) fallback store.
2. **Sequence:** `company_info` pattern-setter first, or batch the trivial ones (term, physical_address,
   company_info — smallest reconciliation footprint) into one unit?
3. **Staging-entity phase (the ~15 raw tables) stays a separate, later program** — it needs the pull
   to stop staging raw payloads, a bigger refactor; out of scope here.
