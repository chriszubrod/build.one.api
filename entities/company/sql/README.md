# Company SQL build order

## Single source of truth

`dbo.company.sql` is the **single canonical source** for the `dbo.Company`
table and all of its stored procedures. No migration may redefine them — change
the base file and apply it. Enforced by `tests/test_sproc_single_source.py`.
Duplicate bodies that drift from the base file break net-zero with prod.

The sprocs: `CreateCompany`, `ReadCompanies`, `ReadCompanyById`,
`ReadCompanyByPublicId`, `ReadCompanyByName`, `UpdateCompanyById`,
`DeleteCompanyById`.

All seven sproc bodies in the base file were stale (pre–Phase 1 shape). They were
reconciled verbatim from `migrations/002_phase1_attribution_sprocs.sql` under
U-140 (2026-07-24).

## ⚠️ Applying this file to prod

The guarded `CREATE TABLE` in the base file predates Phase 1; prod already has
`OrganizationId`, `CreatedByUserId`, and `ModifiedByUserId` from
`migrations/001_phase0_access_control.sql`. Re-applying only the sproc section
is the usual path once bodies are verified base==live. For a fresh database,
follow the build order below.

## From-scratch build order

1. **Prerequisites** — `dbo.User` and `dbo.Organization` must exist (migration
   001's FK batches reference both parent tables).

2. **`entities/company/sql/dbo.company.sql` (first pass)** — the guarded
   `CREATE TABLE` succeeds (Phase-0 shape), but the Phase-1 sproc batches
   **fail** at `CREATE PROCEDURE` time: SQL Server validates columns on
   existing tables, and `OrganizationId`, `CreatedByUserId`, and
   `ModifiedByUserId` are not present yet.

3. **`entities/company/sql/migrations/001_phase0_access_control.sql`** —
   adds `OrganizationId`, `CreatedByUserId`, `ModifiedByUserId`, and related
   Phase-0/1 DDL.

4. **`entities/company/sql/dbo.company.sql` (second pass)** — idempotent
   `CREATE OR ALTER`; all sprocs and the PublicId index batch apply.

## Superseded migration stubs

`migrations/002_phase1_attribution_sprocs.sql` carries a SUPERSEDED banner
(U-140) and no live sproc bodies — re-running it is a no-op for those objects.

`migrations/001_phase0_access_control.sql` remains the authoritative DDL for
Phase-0/1 columns — do not stub that file.
