# UserRole SQL build order

## Single source of truth

`dbo.userrole.sql` is the **single canonical source** for the `dbo.UserRole`
table and all of its stored procedures. No migration may redefine them — change
the base file and apply it. Enforced by `tests/test_sproc_single_source.py`.
Duplicate bodies that drift from the base file break net-zero with prod.

The sprocs: `CreateUserRole`, `ReadUserRoles`, `ReadUserRoleById`,
`ReadUserRoleByPublicId`, `ReadUserRoleByUserId`, `ReadUserRoleByRoleId`,
`ReadUserRolesByUserId`, `UpdateUserRoleById`, `DeleteUserRoleById`,
`ReadUserRolesByUserIdAndCompanyId`.

Nine sproc bodies in the base file were stale (pre–Phase 1 shape). They were
reconciled verbatim from `migrations/002_phase1_company_scoped_sprocs.sql` under
U-144 (2026-07-24). `ReadUserRolesByUserIdAndCompanyId` was homed earlier
(U-126).

## ⚠️ Applying this file to prod — RBAC-critical

UserRole rows feed the Phase-2 permission resolver
(`shared/rbac.py::_resolve_permissions_from_db`). **The base file must match
live prod before any re-apply** — a stale base that drops `@CompanyId` /
attribution params is the 2026-07-15 outage class (SQL 8144, wrong effective
permissions).

The guarded `CREATE TABLE` in the base file predates Phase 0/1; prod already
has `CompanyId`, `CreatedByUserId`, and `ModifiedByUserId` from migration 001.
Uniqueness is the 3-column `UQ_UserRole_UserId_CompanyId_RoleId` from
`scripts/migrations/phase1_not_null_flip.sql` (the old 2-column
`UQ_UserRole_UserId_RoleId` was **dropped** by migration 001 and must never be
re-added). Re-applying only the sproc section is the usual path once bodies are
verified base==live. For a fresh database, follow the build order below.

## From-scratch build order

1. **Prerequisites** — `dbo.User` and `dbo.Role` must exist (FK batches at the
   bottom of the base file need those parent tables).

2. **`entities/user_role/sql/dbo.userrole.sql` (first pass)** — the guarded
   `CREATE TABLE` succeeds (Phase-0 shape), but the Phase-1 sproc batches
   **fail** at `CREATE PROCEDURE` time: SQL Server validates columns on
   existing tables, and `CompanyId` / `CreatedByUserId` / `ModifiedByUserId` are
   not present yet.

3. **`entities/user_role/sql/migrations/001_phase0_access_control.sql`** —
   adds `CompanyId`, `CreatedByUserId`, and `ModifiedByUserId` (and related
   Phase-0/1 DDL).

4. **`scripts/migrations/phase1_not_null_flip.sql`** — adds the 3-column unique
   constraint `UQ_UserRole_UserId_CompanyId_RoleId` (and related NOT NULL
   flips).

5. **`entities/user_role/sql/dbo.userrole.sql` (second pass)** — idempotent
   `CREATE OR ALTER`; all sprocs and FK batches apply.

## Superseded migration stubs

`migrations/002_phase1_company_scoped_sprocs.sql` carries a SUPERSEDED banner
(U-126 + U-144) and no live sproc bodies — re-running it is a no-op for those
objects.

`migrations/001_phase0_access_control.sql` remains the authoritative DDL for
Phase-0/1 columns — do not stub that file.
