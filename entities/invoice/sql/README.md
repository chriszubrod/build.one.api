# Invoice SQL build order

## Single source of truth

`dbo.invoice.sql` is the **single canonical source** for the `dbo.Invoice` table
bootstrap and all 10 of its stored procedures. No migration may redefine them —
change the base file and apply it. Enforced by `tests/test_sproc_single_source.py`.
Duplicate bodies that drift from the base file break net-zero with prod.

The sprocs: `CreateInvoice`, `ReadInvoices`, `ReadInvoiceById`,
`ReadInvoiceByPublicId`, `ReadInvoiceByInvoiceNumber`,
`ReadInvoiceByInvoiceNumberAndProjectId`, `UpdateInvoiceById`, `DeleteInvoiceById`,
`ReadInvoicesPaginated`, `CountInvoices`.

U-158 (2026-07-28) **RECONCILED** the base to the live layer — the base was **STALE**
for `CreateInvoice` (missing `@CreatedByUserId`) and for the three list sprocs
(missing `@ActorUserId` / `@ActorIsSystemAdmin` and the `dbo.UserCanAccessProject`
filter). The live bodies came from `scripts/migrations/gap1_list_sprocs_scoped.sql`
and `scripts/migrations/gap2_core_threading.sql`, which are now stubs.

## ⚠️ Applying this file to prod

Re-applying the **pre-U-158** base file would have **reverted prod**: SQL 8145 on
every invoice create (repo sends `@CreatedByUserId` unconditionally) and on every
invoice list call (repo sends actor params), **and** would have silently removed
UserProject scoping from the invoice list path. Post-U-158, re-applying only the
sproc section is the usual path — the base file now matches live prod.

## From-scratch build order

1. **Prerequisites** — `dbo.Project` and `dbo.PaymentTerm` must exist (FKs on
   `dbo.Invoice`). `dbo.UserCanAccessProject` from `shared/sql/dbo.access_udfs.sql`
   must exist before the list sprocs compile.

2. **`entities/invoice/sql/dbo.invoice.sql` (first pass)** — the guarded
   `CREATE TABLE` and the four `CREATE INDEX` blocks succeed, but the sproc batches
   **fail** at `CREATE PROCEDURE` time: SQL Server validates columns on existing
   tables, and `CreatedByUserId` is not present yet. The guarded `CREATE TABLE` does
   **not** include `CreatedByUserId` (added by
   `scripts/migrations/gap2_created_by_user_id.sql`) or `CompanyId` (phase5
   migrations), yet `CreateInvoice` INSERTs `CreatedByUserId`.

3. **`scripts/migrations/gap2_created_by_user_id.sql`** — adds `CreatedByUserId`
   (and related Gap-2 threading columns on transactional entities).

4. **`entities/invoice/sql/dbo.invoice.sql` (second pass)** — idempotent
   `CREATE OR ALTER`; all sprocs apply.

5. **`scripts/migrations/phase5_company_id_*.sql`** — adds `CompanyId`
   (column → `DEFAULT (1)` → `NOT NULL` flip). ⚠️ Steps 1–4 get the **sprocs** to
   apply, not full schema parity: prod's `dbo.Invoice` carries
   `CompanyId BIGINT NOT NULL`, and the guarded `CREATE TABLE` above never adds it.
   Skip this step and the table is silently divergent from prod. Tracked as the
   campaign-wide "base files are not self-contained from scratch" residual.

The four `CREATE INDEX` blocks and `UQ_Invoice_ProjectId_InvoiceNumber` at the top
of the base file are `IF NOT EXISTS`-guarded and are no-ops against a prod that
already has them.

## Superseded migration stubs

`scripts/migrations/gap1_list_sprocs_scoped.sql` carries SUPERSEDED (U-158) stubs
for `ReadInvoices`, `ReadInvoicesPaginated`, and `CountInvoices` — re-running that
section is a no-op for those objects.

`scripts/migrations/gap2_core_threading.sql` carries a SUPERSEDED (U-158) stub for
`CreateInvoice` — re-running that section is a no-op for that object.
