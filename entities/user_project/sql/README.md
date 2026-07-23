# UserProject SQL build order

## Single source of truth

`dbo.userproject.sql` is the **single canonical source** for the `dbo.UserProject`
table and all 8 UserProject stored procedures. No migration may redefine them —
change the base file and apply it. Enforced by `tests/test_sproc_single_source.py`.
Duplicate bodies that drift from the base file break net-zero with prod.

The 8 sprocs: `CreateUserProject`, `ReadUserProjects`, `ReadUserProjectById`,
`ReadUserProjectByPublicId`, `ReadUserProjectByUserId`, `ReadUserProjectByProjectId`,
`UpdateUserProjectById`, `DeleteUserProjectById`.

Sproc bodies were lifted verbatim from the 004 layer
(`migrations/004_role_qualifier_sprocs.sql`, now a stub) — U-129, 2026-07-23.

## ⚠️ Applying this file to prod

All 8 sproc bodies were verified byte-equivalent to live prod (read-only
`sys.sql_modules` read, 2026-07-23, U-129 Gate-2), so **the sprocs re-apply
cleanly**.

**Uniqueness re-applies as a no-op.** The guarded `CREATE UNIQUE INDEX`
block near the top (`sys.indexes` guard on `UQ_UserProject_UserId_ProjectId`)
is the single canonical uniqueness DDL — the same mechanism prod used via
`scripts/migrations/add_uq_userproject_user_project.sql`, and prod confirms it
(2026-07-23: the name exists in `sys.indexes` as a plain unique index,
`is_unique_constraint = 0`). A trailing `ALTER TABLE … ADD CONSTRAINT … UNIQUE`
block was deleted from this file because it guarded on `sys.objects`, which
does not list a plain unique index; on re-apply that block would attempt
`ADD CONSTRAINT` with a duplicate name and abort the apply.

**But the two FK blocks are a first-time apply on prod.** Verified 2026-07-23:
`sys.foreign_keys` for `dbo.UserProject` has `FK_UserProject_Role`,
`FK_UserProject_CreatedByUser`, `FK_UserProject_ModifiedByUser` — but **not**
`FK_UserProject_User` or `FK_UserProject_Project`. Applying this file would
**CREATE** both for the first time. That is a real schema change, not a no-op —
check for orphan `UserId` / `ProjectId` rows immediately before applying, and
treat the apply as its own gated decision (same situation as
`entities/role_module/sql/README.md`).

## From-scratch build order

1. **`entities/user/sql/dbo.user.sql`** — creates `dbo.User`. Required before
   UserProject because of `FK_UserProject_User`.

2. **`entities/project/sql/dbo.project.sql`** — creates `dbo.Project`.
   Required before UserProject because of `FK_UserProject_Project`.

3. **`entities/role/sql/dbo.role.sql`** — creates `dbo.Role`. Required because
   the sprocs LEFT JOIN `dbo.Role` for `RoleName`.

4. **`entities/user_project/sql/dbo.userproject.sql`** — table, unique index,
   FK constraints, and all 8 sprocs (first apply: table + constraints only if
   columns from steps 5–6 are not yet present).

5. **`entities/user_project/sql/migrations/001_phase0_access_control.sql`** —
   adds `CreatedByUserId` / `ModifiedByUserId` columns.

6. **`entities/user_project/sql/migrations/003_role_qualifier.sql`** — adds
   `RoleId` column + FK to `dbo.Role`.

7. **Re-apply `entities/user_project/sql/dbo.userproject.sql`** — the sprocs
   reference the 001/003 columns, so the base table DDL alone is not sufficient;
   run the sproc section after migrations 001 and 003.

## Superseded migration stubs

`migrations/002_phase1_attribution_sprocs.sql` and
`migrations/004_role_qualifier_sprocs.sql` retain header intent and SUPERSEDED
banners (U-129) but no longer carry live sproc bodies. Re-running them is a
no-op for those objects.

`migrations/001_phase0_access_control.sql` and `migrations/003_role_qualifier.sql`
remain the authoritative DDL for attribution and role-qualifier columns — do not
stub those files.
