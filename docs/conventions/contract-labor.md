# Contract labor

Status workflow, the IsBillable semantics, and the footguns in bill_service.

> Extracted verbatim from `CLAUDE.md` on 2026-11-11 (U-788) to take it off the
> per-session load. **These are standing rules, not history** — they bind the same as
> before. Read this file when touching the area; CLAUDE.md keeps the one-line rule and
> links here.

---

- **Contract Labor status workflow**: `pending_review` → `ready` → `billed`. An entry is `ready` only when it has a `bill_vendor_id` AND at least one complete line item. `pending_review` means line items are missing or incomplete.
- **Contract Labor IsBillable flag**: `ContractLaborLineItem.IsBillable = false` means the item is **not billable to the client** — it renders at `$0.00` on the client-facing PDF (Balance Due excludes it) and pushes to QBO with `BillableStatus=NotBillable`. **It is NOT excluded from the vendor A/P bill** (updated 2026-07-06): `generate_bills_for_vendor` now creates a BillLineItem for non-billable lines too and counts their cost (Hours×Rate) in `Bill.TotalAmount`, because we pay the contractor for every logged hour. Billable and non-billable lines consolidate into **separate** BLIs (keyed on `(SubCostCode, is_billable)`) since they carry different QBO BillableStatus and only billable lines get client markup. Use `is_billable is not False` (not `if is_billable`) to handle `None` (default billable) correctly. (Earlier behavior silently skipped non-billable-only groups → the contractor was under-billed for those hours; pre-2026-07-06 CL bills may be missing non-billable A/P — back-fill separately if needed.)
- **Contract Labor BillLineItemId FK**: `ContractLaborLineItem.BillLineItemId` links back to the generated `BillLineItem`. The UPDATE sproc uses a `CASE WHEN` guard to preserve it when `NULL` is passed. Always read and re-pass the existing value when updating a line item to avoid wiping the FK.
- **Contract Labor bill_service variable shadowing**: In `_generate_combined_pdf()` and `generate_bills()`, inner loop accumulator vars must not be named `total_amount` — use `scc_amount`/`scc_price` to avoid shadowing the outer bill total.
- **Contract Labor bills summary**: The per-vendor aggregation for the React Bills page lives at `entities/contract_labor/business/bill_summary.py::build_bills_summary(billing_period)`. Exposed via `GET /api/v1/contract-labor/bills-summary?billing_period=...`. Returns vendor-grouped dicts with `line_items[]`, day-aggregated `line_items_summary[]` (billed_hours / cost_before_markup / price_after_markup), totals, and date range. Extracted from the deleted Jinja `bills_page` web controller in Wave E3.
- **Contract Labor import tuple unpack**: `_parse_row()` returns `(dict, skip_reason)` — always unpack with `parsed, skip_reason = self._parse_row(...)`. Assigning to a single variable and calling `.get()` on the tuple crashes at runtime.
- **VENDOR_CONFIG**: Single source of truth for vendor rate/markup lives in `entities/contract_labor/business/bill_service.py`. If the React contract-labor pages ever need it client-side, expose via an API endpoint that returns the dict — do not duplicate the config in TypeScript.
