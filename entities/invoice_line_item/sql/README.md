# InvoiceLineItem SQL build order

## Single source of truth

`dbo.invoice_line_item.sql` is the **single canonical source** for the
`dbo.InvoiceLineItem` table bootstrap and all of its stored procedures. No
migration may redefine the sprocs — change the base file and apply it. Enforced
by `tests/test_sproc_single_source.py`. Duplicate bodies that drift from the
base file break net-zero with prod.

The sprocs: `CreateInvoiceLineItem`, `ReadInvoiceLineItems`,
`ReadInvoiceLineItemById`, `ReadInvoiceLineItemByPublicId`,
`ReadInvoiceLineItemsByInvoiceId`, `UpdateInvoiceLineItemById`,
`NullifyInvoiceLineItemsByBillLineItemId`, `DeleteInvoiceLineItemsByBillLineItemId`,
`DeleteInvoiceLineItemById`.

The base bodies were **not** stale — they are the live layer, applied+verified
to prod 2026-07-06 (commit be2a877). U-150 (2026-07-24) only stubbed the stale
migration copy in `migrations/001_2026_05_27_employee_labor_source.sql`.

## ⚠️ Applying `dbo.invoice_line_item.sql` to prod

Re-applying only the sproc section is the usual path — the base file already
matches live prod. For a fresh database, follow the build order below.

## From-scratch build order

1. **Prerequisites** — `dbo.Invoice`, `dbo.BillLineItem`, `dbo.ExpenseLineItem`,
   `dbo.BillCreditLineItem`, and `dbo.SubCostCode` must exist (FK batches at the
   bottom of the base file need those parent tables). `dbo.EmployeeLaborLineItem`
   is optional but required if the `EmployeeLaborLineItemId` FK batch is to
   apply.

2. **`entities/invoice_line_item/sql/dbo.invoice_line_item.sql` (first pass)** —
   the guarded `CREATE TABLE` succeeds, but the sproc batches **fail** at
   `CREATE PROCEDURE` time: SQL Server validates columns on existing tables, and
   `CreatedByUserId` is not present yet. The guarded `CREATE TABLE` does **not**
   include `CreatedByUserId` (added by
   `scripts/migrations/gap2_created_by_user_id.sql`) or `CompanyId` (phase5
   migrations), yet `CreateInvoiceLineItem` INSERTs `CreatedByUserId`.

3. **`scripts/migrations/gap2_created_by_user_id.sql`** — adds `CreatedByUserId`
   (and related Gap-2 threading columns on transactional entities).

4. **`entities/invoice_line_item/sql/dbo.invoice_line_item.sql` (second pass)** —
   idempotent `CREATE OR ALTER`; all sprocs and FK batches apply.

5. **`scripts/migrations/phase5_company_id_*.sql`** — adds `CompanyId`
   (column → `DEFAULT (1)` → `NOT NULL` flip + `IX_InvoiceLineItem_CompanyId`).
   ⚠️ Steps 1–4 get the **sprocs** to apply, not full schema parity: prod's
   `dbo.InvoiceLineItem` carries `CompanyId BIGINT NOT NULL`, and the guarded
   `CREATE TABLE` above never adds it. Skip this step and the table is silently
   divergent from prod. Tracked as the campaign-wide "base files are not
   self-contained from scratch" residual (see `TODO.md`).

## Superseded migration stubs

`migrations/001_2026_05_27_employee_labor_source.sql` carries a SUPERSEDED banner
(U-150) and no live sproc bodies — re-running it is a no-op for those objects.
Its `EmployeeLaborLineItemId` column/FK/index DDL is **retained** and still
authoritative.

## Cross-reference

`dbo.NullifyInvoiceLineItemsByBillCreditLineItemId` is deliberately homed in
`entities/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql` (U-102), NOT
here, even though it mutates `InvoiceLineItem`.
