# Plan — Move markup from ContractLabor aggregation to the Bill stage

_Drafted 2026-06-16. Awaiting approval before implementation._

Live data confirms the analyses exactly: 154 in-scope line items (145 pending_review + 9 ready markup), 64 in-scope parents, 0 already linked to a BillLineItem, EmployeeLabor empty, billed tier (334 markup line items) frozen. All facts verified. I have everything needed to write the plan.

# Implementation Plan: Move Markup from ContractLabor Aggregation to the Bill / BillLineItem Stage

## 1. Summary & Principle

**Change.** Today `dbo.AggregateTimeEntryOnSubmit` bakes markup into the labor line at TimeEntry-submit (`Price = Hours × Rate × (1 + ISNULL(Markup,0))`) and stamps `Markup` onto both `ContractLaborLineItem`/`ContractLabor` and `EmployeeLaborLineItem`/`EmployeeLabor`. Markup is then *un-baked* by `bill_service.generate_bills_for_vendor` (cost = `price/(1+markup)`) and re-applied onto the `BillLineItem`. We are removing markup from the labor stage entirely and applying it for the first time at the billing document.

**Principle (locked):**
- `ContractLabor` / `ContractLaborLineItem` (and `EmployeeLabor` / `EmployeeLaborLineItem`) become **cost-only**: `Price = Hours × Rate`, `Markup = NULL`, parent `TotalAmount = SUM(child cost)`.
- Markup is **first applied at the billing document** — `BillLineItem` for the ContractLabor→Bill path (and, when that path is built, `InvoiceLineItem` for the EmployeeLabor→Invoice path).
- The markup **config is unchanged and stays where it is**: `Vendor.Markup` default (live: 8 @ 0.50, 1 @ 0.35) + `VendorProjectRate.Markup` per-project overrides (live: 5 @ 0.05 on MR2). It is still read through the existing single-source-of-truth resolvers `dbo.ReadEffectiveRateForVendorProject` / `dbo.ReadEffectiveRateForEmployeeProject` (wrapped by `VendorProjectRateService.read_effective_rate(*, vendor_id, project_id)` and `EmployeeProjectRateService.read_effective_rate`). It is just **read one stage later**.

**Premise correction (verified against the deployed object = migration 009).** The deployed sproc does **not** put a vendor-default rate/markup on a multi-project parent — it sets a multi-project parent's `ProjectId/Rate/Markup/Amount = NULL`, `RateSource='multi_project'`, and only a single-project parent carries a rate+markup (mirroring its one bucket). That multi-project block is already cost-only-compatible and needs no change. The "vendor-default on multi-project parent" description matches the superseded migration 001 (per-project parent), not prod.

**Verified live state** (drives migration scope): ContractLabor line items — billed `334 markup / 22 cost`, pending_review `145 markup / 3 cost`, ready `9 markup / 1 cost`. Parents — billed `338 cost/NULL`, pending_review `64 markup / 109 cost/NULL`, ready `10 cost/NULL`. **In scope = 154 line items + 64 parents** (pending_review + ready, `Markup<>0`). `EmployeeLabor`/`EmployeeLaborLineItem` = **0 rows**. **0** in-scope line items are linked to a `BillLineItem` (safe to revert).

---

## 2. Code Changes

### 2A. Aggregation sproc — make labor cost-only
**New file:** `entities/time_entry/sql/migrations/012_2026_06_16_aggregate_cost_only.sql` (`CREATE OR ALTER PROCEDURE dbo.AggregateTimeEntryOnSubmit`, full body copied verbatim from deployed/009, with only the markup math changed). Run via `python scripts/run_sql.py`. **Do not edit 009 in place** — it is the historical record of the prior deployed state; add a header noting 012 supersedes 009 for markup deferral, and that the baked-markup lines it removes were introduced in 008/009.

Preserve everything else identically: XOR Employee/Vendor branch, billing-period math, project bucketing + `ConcatNotes`, **parent-upsert-outside-cursor keyed on `SourceTimeEntryId`** (the 009 bug-fix), line-item key `(ParentId, SourceTimeEntryId, ProjectId)` with the NULL-project defend, `Status='billed'`/`'invoiced'` frozen-state guards, `@Results` shape, final `SELECT`. Edits (apply symmetrically in the Employee and Vendor branches):

- **Single-project parent (deployed line 176):** `SET @ParentAmount = @ParentTotalHrs * @ParentRate * (1 + ISNULL(@ParentMarkup, 0));` → `SET @ParentAmount = @ParentTotalHrs * @ParentRate;` and add `SET @ParentMarkup = NULL;`. Still call `ReadEffectiveRateForVendorProject`/`...EmployeeProject` to obtain `@ParentRate` (rate config still resolves); just null the markup before persisting. Leave the `@ParentRate IS NULL` ELSE branch (`'Rate not configured...'`, `rate_source=none`) untouched.
- **Per-bucket line item (deployed line 333):** `SET @TotalAmount = @TotalHours * @HourlyRate * (1 + ISNULL(@Markup, 0));` → `SET @TotalAmount = @TotalHours * @HourlyRate;` and add `SET @Markup = NULL;` *after* `@RateSource` is read from the resolver (so the `rate_source` diagnostic still reflects override/default/none) but before the INSERT/UPDATE. Leave the `@HourlyRate IS NULL` ELSE (`rate_source=none for Project Id=...`) untouched.
- **Multi-project parent block (deployed ~188–195):** leave verbatim — already NULL rate/markup/amount.
- **Both parent + both line-item INSERT/UPDATE statements:** no column-list change — they already reference `@ParentMarkup`/`@Markup`/`@ParentAmount`/`@TotalAmount`, which are now nulled/cost-only. Confirm all four write sites read the mutated locals.
- **`@Results` + final SELECT:** keep the `Markup`/`RateSource` columns for row-shape stability; `Markup` simply carries NULL now. The Python caller only logs them.

> Keep all arithmetic in T-SQL DECIMAL (`Hours DECIMAL(6,2) * Rate DECIMAL(18,4)` → `DECIMAL(18,2)`). No Python float.

### 2B. `entities/contract_labor/business/bill_service.py` — apply markup at the bill (primary forward change)
**Imports + `__init__`:** add `from entities.vendor_project_rate.business.service import VendorProjectRateService` and instantiate `self.vpr_service`.

**`generate_bills_for_vendor` consolidation loop (lines ~310–344):** remove the inverse derivation entirely (do not layer on top). Today:
```
markup_val = Decimal(str(li.markup or 0)); price = Decimal(str(li.price or 0))
amount = price / (Decimal("1") + markup_val) if markup_val else price   # cost
... effective_markup = (scc_price - scc_amount) / scc_amount
BillLineItem(rate=scc_amount, amount=scc_amount, markup=effective_markup, price=scc_price)
```
New logic — `li.price` is now **cost**, `li.markup` is **NULL**:
- One bill per project (`group_key = li.project_id`, `None`=overhead). Markup is **constant within a bill**, so resolve **once per bill**: `eff = self.vpr_service.read_effective_rate(vendor_id=vendor.id, project_id=project_id)`; `markup = Decimal(str(eff.get("markup") or 0))`. `project_id=None` (overhead) resolves to `Vendor.Markup` default via the sproc.
- Per SubCostCode group: `scc_cost = sum(Decimal(str(li.price or 0)) for billable lines)`; `scc_price = scc_cost * (Decimal("1") + markup)`.
- `BillLineItem(rate=scc_cost, amount=scc_cost, markup=markup, price=scc_price)` — store the **resolved per-project markup** (e.g. `0.50`/`0.05`), not a back-computed ratio. This preserves the QBO contract (`_build_qbo_line` sends `amount`=cost + `MarkupInfo.Percent = markup*100` and QBO computes the marked-up total — `amount` must stay cost, never marked-up, or QBO double-applies).

**Transition guard (REQUIRED if 2A/§3 don't ship in the same cutover):** live line items are mixed (some still `markup=0.50`/marked-up). Detect legacy rows by `li.markup IS NOT NULL`: if non-null, treat `li.price` as already marked-up (old back-compute) and skip the new `*(1+markup)`; if NULL, apply the new cost→marked-up math. The clean alternative is to backfill (§3) and deploy together so no row is ambiguous — preferred; the dual-mode guard is the fallback.

**Bill total + preview/regenerate totals (lines ~231, 385, 532, 685):** `total_amount = sum(li.price)` becomes cost-only after the change and would under-bill by the markup factor. Recompute from the new marked-up figures (sum of `BillLineItem.price`, i.e. `scc_cost*(1+markup)`) at **every** total site — `generate`, `preview_pdf_for_vendor`, `_generate_combined_pdf`, `regenerate_pdf_for_entries`. Keep `float()` only at the final reportlab render boundary; do the math in Decimal.

**PDF line AMOUNT (line ~877) `_generate_pdf_elements`:** renders `float(li.price)` as the customer-facing amount; `li.price` is now cost. Render the marked-up figure — preferably read the persisted `BillLineItem.price`. For `regenerate_pdf_for_entries` (already-billed entries) read the **persisted** `BillLineItem.price` (stable), not a live re-resolve, so a config edit between generate and regenerate can't drift the saved bill.

### 2C. `entities/contract_labor/business/bill_summary.py` — Generate-Bills preview feed
React `ContractLaborBills.tsx` reads `cost_before_markup` / `price_after_markup` / `total_amount`. Currently `price_after_markup = li.price` (echoes the row) and `cost_before_markup = (hours/8.0)*rate`. After the change `li.price` is cost. Recompute `price_after_markup` by **applying the resolved markup config** to cost (same `read_effective_rate` call, same per-project resolution as 2B) and recompute `total_amount` as the marked-up total, so the preview matches the bill that will be produced. Fix the latent `(hours/8.0)*rate` bug — cost basis is `hours*rate` (hourly), matching the line item; the `/8` daily divisor is wrong now that markup is split out and would surface a preview/bill mismatch.

### 2D. ContractLabor manual/legacy write paths — cost-only consistency
`entities/contract_labor/business/service.py` `create()` + `update_by_public_id()` and `entities/contract_labor/business/model.py` `calculate_total_amount()` compute `total_amount = hours*rate*(1+markup)`. Change to cost-only (`total_amount = hours*rate`, `markup=NULL`) so all CL write paths agree with the new sproc.

### 2E. EmployeeLabor → Invoice path — **DEFER (decision needed, §7)**
EmployeeLabor never produces a Bill; it is invoiced directly, and the picker is unbuilt (0 rows everywhere; `InvoiceService.get_billable_items_for_project` queries only Bill/Expense/BillCredit; `_SOURCE_TYPE_TO_FK_FIELD`, `_mark_source_as_billed`, `_reset_source_as_unbilled` omit `EmployeeLaborLineItem`). If the owner confirms employee labor carries markup to the customer, do it **when that picker is built** (not now):
- Add an `EmployeeLaborLineItem` branch to `get_billable_items_for_project` (`entities/invoice/business/service.py` ~603–851): surface cost-only ELLI rows; resolve markup **per row** via `EmployeeProjectRateService.read_effective_rate(employee_id, project_id)` (markup is **not** constant within an invoice, which spans many projects — must resolve per line, unlike the Bill side).
- `entities/invoice/intelligence/tools.py`: add `'EmployeeLaborLineItem': 'employee_labor_line_item_id'`.
- `_mark_source_as_billed` / `_reset_source_as_unbilled`: add the ELLI branch (freeze via parent `EmployeeLabor.status='invoiced'` + `InvoiceLineItemId`; ELLI has no `IsBilled` column — confirm freeze mechanism). Strip markup from the EmployeeLabor branch in sproc 2A **only when** this re-application is live, else employee invoices under-bill.

### 2F. React surfaces (display/input only — won't crash, will mislead until reworked)
- **CL pages** (`ContractLaborView.tsx`, `ContractLaborEdit.tsx`, `labor/LaborReviewScreen.tsx`, `contract-labor/ContractLaborBills.tsx`): present/persist cost-only. **`LaborReviewScreen.tsx` must stop SAVING `markup` + marked-up `price` onto CL line items** (write `hours*rate` cost, `markup=null`) — otherwise it re-bakes markup outside the sproc. Relabel/remove the markup/Price columns on CL surfaces or make them informational.
- **Bill surfaces** (`BillEdit.tsx`, `BillView.tsx`): markup % is now shown/editable here for CL-sourced `BillLineItem`s (`BillLineItem` already has `markup`+`price`). Move the `VENDOR_CONFIG`/MR2-5% display from CL to the bill stage.

### 2G. `dbo.ReadBudgetVarianceByProjectId` — no change required (optional cleanup)
`ContractLaborAgg` computes `SUM(Price / NULLIF(1+ISNULL(Markup,0),0))`; with `Markup=NULL` this degrades to `Price/1 = cost` — still correct. `EmployeeLaborAgg` already uses `Hours*Rate`; `DrawnAgg` reads `InvoiceLineItem.Price` (customer-facing) — both unaffected. Optional cosmetic simplification to `SUM(Price)` is out of scope for the cutover (minimize blast radius).

**Confirmed unaffected:** QBO bill sync (`_build_qbo_line` reads `BillLineItem.amount`+`markup` only), MS Excel/outbox (reads `BillLineItem`/invoice, not CL), `enrichment.py` EmployeeLabor block (display labels only), `scripts/_clr_remittance.py` (computes from `TimeLog` with its own hardcoded rate, intentionally cost-only), `ReadContractLaborDailySummary` (hours only).

---

## 3. Data Migration

**New file:** `entities/contract_labor/sql/migrations/2026_06_16_revert_markup_to_cost_only.sql`, run via `python scripts/run_sql.py`. Single transactional, idempotent, re-runnable migration. **Two safety findings drive the SQL** (verified live): (1) recover cost via `Price/(1+Markup)`, **never** `Hours*Rate` — 8 'ready' line items (Ids 357–364, parents 498–509) store `Price = Rate*(1+Markup)` with Hours excluded (`Hours*Rate` would corrupt 240→1920); (2) recompute parent `TotalAmount` as `SUM(cost-only child Price)`, **never** parent `Hours*HourlyRate` — in 20 of 64 parents the parent's full daily hours ≠ summed billable line hours. Bill generation walks line items, so line items are authoritative.

**STEP A — line items first** (so STEP B's SUM reads cost-only children):
```sql
UPDATE li
SET Price = CASE WHEN li.Price IS NOT NULL AND li.Markup IS NOT NULL AND li.Markup <> 0
                 THEN CAST(ROUND(li.Price / (1 + li.Markup), 2) AS DECIMAL(18,2))
                 ELSE li.Price END,
    Markup = NULL,
    ModifiedDatetime = SYSUTCDATETIME()
FROM dbo.ContractLaborLineItem li
JOIN dbo.ContractLabor cl ON cl.Id = li.ContractLaborId
WHERE cl.Status IN ('pending_review','ready')
  AND li.Markup IS NOT NULL AND li.Markup <> 0
  AND li.BillLineItemId IS NULL;   -- belt-and-suspenders (verified 0 in scope)
```
The 1 NULL-Price row (Id 365) just gets `Markup` nulled.

**STEP B — parents:**
```sql
UPDATE cl
SET TotalAmount = (SELECT CAST(ROUND(SUM(li.Price),2) AS DECIMAL(18,2))
                   FROM dbo.ContractLaborLineItem li WHERE li.ContractLaborId = cl.Id),
    Markup = NULL,
    ModifiedDatetime = SYSUTCDATETIME()
FROM dbo.ContractLabor cl
WHERE cl.Status IN ('pending_review','ready')
  AND cl.Markup IS NOT NULL AND cl.Markup <> 0;
```
Wrap both in `SET NOCOUNT ON` + explicit `BEGIN/COMMIT TRANSACTION`; `PRINT @@ROWCOUNT` after each step (expect STEP A ≈ 154, STEP B ≈ 64).

**Exclusion rules:** `Status IN ('pending_review','ready')` only — **never** touch `'billed'` (CL) or `'invoiced'` (EmployeeLabor, empty). `Markup IS NOT NULL AND Markup <> 0` makes already-reverted Vendor 1184 rows (parents 611/612, line items 512–515) automatic no-ops. **EmployeeLabor: no migration** (0 rows); do not write EL UPDATEs that could fire later. Note: parents 598/599 are multi-project yet carry a pre-009 non-null parent Markup — STEP B correctly nulls it and rebases from children; flag to owner that two should-be-NULL parents existed.

**Verification queries** (PRE before apply, POST after — include as trailing SELECTs or a sibling `_verify` script):
1. POST: in-scope remaining = 0 — `... cl.Status IN ('pending_review','ready') AND li.Markup IS NOT NULL AND li.Markup<>0` → 0; same for parents → 0.
2. Frozen tier intact — `... cl.Status='billed' AND li.Markup IS NOT NULL AND li.Markup<>0` → **still 334**; billed parents → 338.
3. Per-parent reconcile — every touched parent: `ABS(TotalAmount - SUM(child Price)) <= 0.02`.
4. Spot-checks: 611/612 + 512–515 unchanged; parent 513 → `TotalAmount 240.00`; parents 598/599 → `260.00` / `370.01`; the 8 per-day rows (357–364) → cost (`240/260/370/500…`), **not** 1920+.

---

## 4. Deploy Steps (ordering matters — no window of wrong billing)

Prod API = Docker (`az acr build` + `az webapp restart`); SQL = `scripts/run_sql.py`. The two hard couplings: (a) the aggregator must go cost-only **before/with** the data migration or any reject→edit→resubmit re-bakes markup and silently undoes the migration (the sproc's UPDATE branch overwrites Hours/Rate/Markup/Price); (b) `bill_service` must apply markup **before** any cost-only row is billed or that bill goes out at cost.

Recommended single-cutover order:

1. **Pre-flight** — run §3 PRE verification (capture baseline counts: 334 billed-markup, 154/64 in scope).
2. **Deploy API code** (§2B–2D, §2F backend pieces) via Docker: `az acr build` then `az webapp restart`. Ship with the §2B dual-mode guard (`li.markup IS NOT NULL` ⇒ treat price as already-marked-up) so the still-mixed live data is billed correctly during the brief gap before SQL lands.
3. **Deploy aggregator sproc** (§2A, migration 012): `python scripts/run_sql.py entities/time_entry/sql/migrations/012_2026_06_16_aggregate_cost_only.sql`. From here, new/resubmitted aggregations are cost-only.
4. **Run data migration** (§3): `python scripts/run_sql.py entities/contract_labor/sql/migrations/2026_06_16_revert_markup_to_cost_only.sql`, then immediately run §3 POST verification.
5. **Deploy React** (`build.one.web` — local `npm run dev` against prod, no deployed host; merge/push the branch so the CL surfaces stop saving markup and the bill surface exposes it).
6. **Remove the §2B dual-mode guard** in a follow-up deploy once verification confirms no `markup<>0` non-terminal rows remain — at that point all non-terminal CL is cost-only and the guard is dead code. (Keep it through one verified cycle.)

Steps 3 and 4 are adjacent and fast; the only "wrong billing" exposure is a `bill_service` that bills a cost-only row at cost — eliminated because §2B (markup application) is live from step 2.

---

## 5. Verification (prove the new world)

**A. Labor is cost-only.** Re-run §3 POST asserts (in-scope markup count = 0 for parents and line items; per-parent `ABS(TotalAmount - SUM(child Price)) ≤ 0.02`). Submit a fresh TimeEntry against the new sproc and confirm the new `ContractLaborLineItem` has `Markup IS NULL` and `Price = Hours*Rate`, and the parent `TotalAmount = SUM(child Price)`, `Markup IS NULL` (multi-project) or cost-only (single-project).

**B. Bill carries correct markup.** Generate a bill for a default-markup vendor (e.g. one of the 8 @ 0.50) on a non-MR2 project and assert each `BillLineItem`: `amount` = cost (`SUM` of cost-only CL line prices in the SCC group), `markup = 0.50`, `price = amount * 1.50`, and `Bill.total_amount = SUM(BillLineItem.price)`. Generate a bill for an **MR2** project (VendorProjectRate override, e.g. Vendor 1184) and assert `markup = 0.05`, `price = amount * 1.05`. Generate an **overhead** bill (`project_id=None`) and assert it resolves to the Vendor default markup (`rate_source='default'`), not 0.

**C. Numbers tie out at the bill level.** For a converted-to-cost-only `ready` row, confirm `cost_before_markup * (1 + resolved_markup)` equals the old baked `Price` to the cent (i.e. field-crew billed amounts are unchanged at the bill — markup just moved stages). Confirm the Generate-Bills preview total (`bill_summary.py` / `ContractLaborBills.tsx`) equals the generated `Bill.total_amount` and the PDF Balance Due (three total computations must agree).

**D. QBO / downstream unchanged.** Confirm `_build_qbo_line` still sends `amount`=cost + `MarkupInfo.Percent = markup*100` (QBO computes the marked-up total once — no double-apply). Confirm `ReadBudgetVarianceByProjectId` ContractLaborCost is unchanged (it reads cost both before and after).

---

## 6. Rollback

- **Code (§2B–2F):** redeploy the previous API Docker image (`az webapp` revert/redeploy prior tag) + revert the React branch. The previous `bill_service` expects marked-up CL rows with `markup<>0`.
- **Aggregator sproc:** re-run migration 009 (`python scripts/run_sql.py entities/time_entry/sql/migrations/009_2026_06_03_aggregate_parent_per_time_entry.sql`) — it is `CREATE OR ALTER`, restoring the markup-baking body. 012 leaves 009 untouched, so the rollback source is intact.
- **Data:** the §3 migration is the lossy step. Reversing requires re-baking markup onto the 154 line items + 64 parents (`Price = Price * (1 + resolved_markup)`, restore `Markup`, restore parent `TotalAmount`). **Take a pre-migration snapshot of `Id, Markup, Price` for the in-scope `ContractLaborLineItem` rows and `Id, Markup, TotalAmount` for the in-scope parents** (e.g. `SELECT … INTO #cl_markup_backup_20260616`) immediately before step 4 so rollback is an exact `UPDATE … FROM backup` join, not a recompute. The frozen `billed` tier is never touched, so prod bills are safe regardless. Because 0 in-scope rows are linked to a `BillLineItem`, no Bill needs regenerating on rollback.

---

## 7. Open Questions / Decisions for the Owner

1. **EmployeeLabor markup (most important).** EmployeeLabor never produces a Bill — it invoices directly, and the picker is unbuilt (0 rows). Does employee labor carry markup to the customer? If **yes**, markup must re-appear at `InvoiceLineItem` (§2E), built when the employee→invoice picker is built; the aggregator's EmployeeLabor branch must be stripped of markup **only at that point** (not in this cutover) or employee invoices under-bill. If **no** (employee labor billed at cost), nothing further. The prompt names only ContractLabor as the target — explicit confirmation needed. **Recommendation: leave EmployeeLabor aggregation markup-bearing for now** (empty table, no path), defer to the picker project.
2. **`BillLineItem.markup` semantics going forward.** Store the resolved per-project markup (`0.50`/`0.05`) for transparency + QBO `MarkupInfo`, or NULL it? **Recommendation: store the resolved markup** (QBO needs the percent; today it stored a back-computed ratio). Confirm nothing downstream asserts the old effective-ratio meaning.
3. **Backfill `ready` rows imminently being billed.** 10 `ready` parents / 9 markup line items — confirm the Bill markup-application path (§2B) is verified live **before** converting them, else a `ready`-then-billed row would ship at cost. (Deploy order in §4 already enforces this; flag any `ready` row queued to bill during the cutover window.)
4. **Config freeze vs live resolve at bill time.** `bill_service` re-calls `read_effective_rate` at bill time (authoritative; picks up later config edits). Confirm the owner wants markup to reflect config **as of bill generation**, not as of TimeEntry submit. **Recommendation: bill-time resolve** (matches "markup first applied at the billing document").
5. **`bill_summary.py` cost basis.** The pre-existing `(hours/8.0)*rate` `cost_before_markup` is wrong (hours is total, not days). Confirm cost basis is `hours*rate` so the preview matches the generated bill. **Recommendation: fix to `hours*rate`.**
6. **Anomalous multi-project parents 598/599.** They carry a pre-009 non-null parent Markup. The migration auto-nulls it and rebases `TotalAmount` from children (safe default). Confirm no manual review wanted.
7. **CL React surface markup columns.** Remove markup/Price columns from CL pages entirely, or keep them read-only/informational? This drives how aggressively `LaborReviewScreen.tsx` is reworked (it must, at minimum, stop *saving* markup).
8. **`ReadBudgetVarianceByProjectId` cleanup.** Simplify the now-dead inverse-markup divide to `SUM(Price)`, or leave the harmless no-op to minimize blast radius? **Recommendation: leave for the cutover, clean up later.**

**Relevant files (absolute):**
- `/Users/chris/Applications/build.one/build.one.api/entities/time_entry/sql/migrations/009_2026_06_03_aggregate_parent_per_time_entry.sql` (supersede via new `012_2026_06_16_aggregate_cost_only.sql` in the same dir)
- `/Users/chris/Applications/build.one/build.one.api/entities/contract_labor/business/bill_service.py`
- `/Users/chris/Applications/build.one/build.one.api/entities/contract_labor/business/bill_summary.py`
- `/Users/chris/Applications/build.one/build.one.api/entities/contract_labor/business/service.py`, `.../model.py`
- `/Users/chris/Applications/build.one/build.one.api/entities/vendor_project_rate/business/service.py` (`read_effective_rate(*, vendor_id, project_id)`)
- `/Users/chris/Applications/build.one/build.one.api/entities/invoice/business/service.py`, `.../intelligence/tools.py`, `.../business/enrichment.py` (employee path, deferred)
- New data migration: `/Users/chris/Applications/build.one/build.one.api/entities/contract_labor/sql/migrations/2026_06_16_revert_markup_to_cost_only.sql`
- React: `/Users/chris/Applications/build.one/build.one.web/src/pages/contract-labor/{ContractLaborView,ContractLaborEdit,ContractLaborBills}.tsx`, `.../labor/LaborReviewScreen.tsx`, `.../bills/{BillEdit,BillView}.tsx`
