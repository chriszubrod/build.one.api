# UserCompany SQL build order

## Single source of truth

`dbo.usercompany.sql` is the **single canonical source** for the `dbo.UserCompany`
table bootstrap and all of its stored procedures. No migration may redefine the
sprocs — change the base file and apply it. Enforced by `tests/test_sproc_single_source.py`.
Duplicate bodies that drift from the base file break net-zero with prod.

The sprocs: `CreateUserCompany`, `ReadUserCompanies`, `ReadUserCompanyById`,
`ReadUserCompanyByPublicId`, `ReadUserCompanyByUserId`, `ReadUserCompaniesByUserId`,
`UpdateUserCompanyById`, `DeleteUserCompanyById`.

Eight sproc bodies in the base file were stale (pre–Phase 1 shape). They were
reconciled verbatim from `migrations/002_phase1_attribution_sprocs.sql` under
U-146 (2026-07-24).

## ⚠️ Applying this file to prod

**The base file must match live prod before any re-apply** — a stale base that
drops attribution params is the 2026-07-15 outage class (SQL 8144 parameter
errors).

The guarded `CREATE TABLE` in the base file predates Phase 0/1; prod already
has `CreatedByUserId` and `ModifiedByUserId` from migration 001. Re-applying
only the sproc section is the usual path once bodies are verified base==live.
For a fresh database, follow the build order below.

## From-scratch build order

1. **Prerequisites** — `dbo.User` and `dbo.Company` must exist (FK batches at the
   bottom of the base file need those parent tables).

2. **`entities/user_company/sql/dbo.usercompany.sql` (first pass)** — the guarded
   `CREATE TABLE` succeeds (Phase-0 shape), but the Phase-1 sproc batches
   **fail** at `CREATE PROCEDURE` time: SQL Server validates columns on
   existing tables, and `CreatedByUserId` / `ModifiedByUserId` are not present
   yet.

3. **`entities/user_company/sql/migrations/001_phase0_access_control.sql`** —
   adds `CreatedByUserId` and `ModifiedByUserId` (and related Phase-0/1 DDL).

4. **`entities/user_company/sql/dbo.usercompany.sql` (second pass)** — idempotent
   `CREATE OR ALTER`; all sprocs and FK batches apply.

## Superseded migration stubs

`migrations/002_phase1_attribution_sprocs.sql` carries a SUPERSEDED banner
(U-146) and no live sproc bodies — re-running it is a no-op for those objects.

`migrations/001_phase0_access_control.sql` remains the authoritative DDL for
Phase-0/1 columns — do not stub that file.
