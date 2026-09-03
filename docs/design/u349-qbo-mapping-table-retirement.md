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

## 7. Canonical running order (2026-09-01, post-census)

Unit ids == running position. Two axes drive complexity: **reconciliation JOINs** (force a dbo-native
re-expression of drift queries in `reconciliation/business/service.py`) and **connector type**:

- **Header connectors** (`run_identity_fastpath` → repoint onto the EXISTING
  `run_identity_fastpath_dbo_only`): company_info, physical_address, term, vendorcredit, purchase,
  bill, invoice. These CLONE U-350 directly — no new primitive.
- **Line-item connectors** (`run_line_identity_fastpath`, parent+line identity): bill_line_item,
  invoice_line_item, vendorcredit_line_item, expense_line_item. **There is NO
  `run_line_identity_fastpath_dbo_only` yet** — the FIRST line-item retirement is FOUNDATIONAL:
  it builds that shared helper (the line-item analog of `run_identity_fastpath_dbo_only`) and is
  therefore **two-phase / design-gated** (`feedback_two_phase_dispatch_design_gated.md`), not a clone.

| # | Family (mapping) | Unit | Connector | Recon | Real cross-family | Complexity | Status |
|---|---|---|---|---|---|---|---|
| 1 | company_info (CompanyInfoCompany) | U-350 | header | 0 | — | pattern-setter | ✅ done+dropped |
| 2 | physical_address (PhysicalAddressAddress) | U-351 | header | 0 | — | clean clone | in flight |
| 3 | term (TermPaymentTerm) | U-352 | header | 0 | bill SalesTermRef + sync script | medium | prompt ready |
| 4 | vendorcredit (VendorCreditBillCredit) | U-353 | header | 0 | vendorcredit entity-SQL write | medium | queue |
| 5 | purchase/expense (PurchaseExpense) | U-354 | header | 0 | outbox worker + invoice source-link + purchase svc/router | medium-heavy | queue |
| 6 | bill (BillBill) | U-355 | header | 0 | base/compensation + base/identity_consistency + outbox + bill_line_item connector | heavy (solo) | queue |
| 7 | invoice (InvoiceInvoice) | U-356 | header | **1** | reconciliation + outbox worker | hard (recon) | queue |
| 8 | vendorcredit_line_item (…BillCreditLineItem) | U-361 | **line-item** | 0 (Map: 2 executed consumers in `vendorcredit/business/service.py`, both repointed) | — | **FOUNDATIONAL: builds `run_line_identity_fastpath_dbo_only`; two-phase** | ✅ built 2026-09-01 (Gate-2 pending) |
| 9 | invoice_line_item (InvoiceLineItemInvoiceLine) | U-362→362b→362c | line-item | 0 | invoice source-link | clone (needs #8's helper); +3 adversarial-caught money bugs on source-linked-line collision (28,979 shared-LinkedTxn lines) | ✅ done+deployed+dropped 2026-09-03 (cd03ca84 / ACR caak; table 30,513 rows dropped) |
| 10 | bill_line_item (BillLineItemBillLine) | U-363 | line-item | **1** | reconciliation + bill svc | hard (recon); flag-only recon re-expression + push-stamp-verify + concurrent-delete-race fix | ✅ done+deployed+dropped 2026-09-03 (745d7285 / ACR caam; table 23,678 rows dropped; census 0 strand risk; adversarial 0 money bugs) |
| 11 | expense_line_item (PurchaseLineExpenseLineItem) | U-364 | line-item | **1** | reconciliation + purchase svc/router | hard (recon) | queue |

**Ordering logic:** header families first (existing helper, ascending cross-family surface), the one
header-with-recon (invoice) at the end of the header block; then the line-item block led by the
FOUNDATIONAL helper build (#8, simplest line-item so the primitive is proven cleanly), then clone it
across the remaining line-items, recon ones last.

### Collision rules (parallelism)
- **`base/identity_drift.py` registry + `tests/test_identity_drift_bulk_read.py` count assertion —
  EVERY header retirement (#3–#7) removes its `FlatEntitySpec` row AND the `untouched_keys` count
  test decrements. Two header units built in parallel each decrement from the SAME stale base →
  the double-decrement bug (U-351+U-352 landed both at "3" when the true combined count was 2;
  caught only in rebase). SERIALIZE header retirements, OR on rebase RECOMPUTE the count against
  the actual registry contents (not the stale base) — never trust the pre-rebase assertion.**
  (Line-item families #8–#11 are not FlatEntitySpec header entities, so they don't touch this.)
- `reconciliation/business/service.py` — #7, #10, #11 → **strictly serial**, never concurrent.
- `outbox/business/worker.py` — #5, #6, #7 → not concurrent with each other.
- `base/compensation.py` / `base/identity_consistency.py` — #6 (BillBill) touches shared base → run **solo**.
- `base/identity_fastpath.py` — #8 ADDS the line-item dbo-only helper; #9/#10/#11 CONSUME it → #8 must
  land (Gate-2 + merged) before #9–#11 dispatch; those three then serialize on reconciliation anyway.
- `invoice/connector/invoice` source-link — read by #5 and #9 → serialize those two.

### Pre-DROP live-dependency check — STRIP COMMENTS FIRST (U-362c gotcha, applies to #10/#11)
Before dropping a mapping table, enumerate the live sprocs that still reference it — but a raw
`sys.sql_modules.definition LIKE '%<MappingTable>%'` yields **false positives from the table name lingering
in COMMENTS of sprocs already re-homed onto the `dbo` mirror**. In U-362c three sprocs
(`ReadInvoiceLineItemsByInvoiceId`, `ProposeInvoiceSourceLinks`, `ReadInvoiceSourceLinkLines`) matched only
in comments (re-homed onto `dbo.InvoiceLineItemSourceProvenance` back in U-272); the real body-refs were just
the 5 pure CRUD sprocs. **Strip `/* */` and `--` comments before deciding a DROP blocker**, then confirm the
survivors are unreferenced by the DEPLOYED code. Expect the same false positives for #10 (`BillLineItemBillLine`)
and #11 (`PurchaseLineExpenseLineItem`) in the re-expressed reconciliation sprocs.

### Wave cadence
- **Wave A (headers, clone-able, 1–2 at a time):** #3 term → #4 vendorcredit → #5 purchase/expense.
- **Wave B (heavy headers, solo):** #6 BillBill (base-helper blast radius) → #7 invoice (recon re-expression).
- **Wave C (line-items):** #8 vendorcredit_line_item DESIGN → /em → BUILD (creates the helper) → then
  #9 invoice_line_item → #10 bill_line_item → #11 expense_line_item (serial on reconciliation).
- After enough families land on the shape, fold in the deferred conflict-predicate `/simplify`
  extraction into `base/identity_fastpath.py` (U-350-booked cross-cutting cleanup).

Net at completion: `qbo.*` 30 → **19** (11 mapping tables gone); the ~15 raw-staging entity tables
(now unblocked, their mapping children removed) become the separate Phase-4/5 program; keep-set stays
`qbo.Auth` + `Outbox`/`ReconciliationIssue`/`ApiUsage`/`Client`.
