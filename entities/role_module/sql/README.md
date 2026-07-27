# RoleModule SQL build order

## Single source of truth

`dbo.rolemodule.sql` is the **single canonical source** for the `dbo.RoleModule`
table (including the `CanViewTeam` column) and all 8 RoleModule stored
procedures. No migration may redefine them — change the base file and apply it.
Enforced by `tests/test_sproc_single_source.py`. Duplicate bodies that drift from
the base file break net-zero with prod.

`DeleteRoleModuleById` deliberately omits `CanViewTeam` from its `OUTPUT DELETED`
list to stay byte-identical with the deployed prod body.

## ⚠️ Applying this file to prod is NOT a no-op until U-053's migration has run

All 8 sproc bodies here were verified byte-equivalent to live prod (read-only
`sys.sql_modules` read, 2026-07-16), so **the sprocs re-apply cleanly**. The
`CanViewTeam` ALTER is `IF NOT EXISTS`-guarded and inert (prod has the column).

**The constraints are the exception.** As of the U-048 prod read, `sys.foreign_keys`
for `dbo.RoleModule` returns zero rows and the only constraint present is the PK, so
applying this file would **CREATE** `FK_RoleModule_Role`, `FK_RoleModule_Module` and
`UQ_RoleModule_RoleId_ModuleId` for the first time — a real schema change, not a
no-op. **U-053 does that deliberately instead**, via the explicit, data-guarded,
self-verifying migration `migrations/001_rbac_join_integrity_constraints.sql`, so
the change is auditable rather than an untracked side effect of a base re-apply.
The declarations stay here as the canonical schema; the migration is the apply
vehicle.

**Apply status:** the migration is committed, but running it against prod is its
own gated decision and may not have happened yet. Until it has, treat this file as
NOT safe to blind-apply. Verify with the read-back query in `TODO.md`
(`sys.foreign_keys` + `sys.indexes` for the four constraint names); once all four
verify, base == live for the constraints too and this file becomes a genuine no-op.

Why they were never applied before: `FK_RoleModule_Role` had no `GO` before it, so
it was swallowed into `DeleteRoleModuleById`'s body — a `CREATE PROCEDURE` body
runs to the end of the **batch**, not to its matching `END`. U-048 added the
missing `GO`. Watch for that shape when converting other entities; U-053 found a
third instance in `entities/module/sql/dbo.module.sql` and fixed it there.

## From-scratch build order

1. **`entities/role/sql/dbo.role.sql`** — creates `dbo.Role`. Required before
   RoleModule because of `FK_RoleModule_Role`.

2. **`entities/module/sql/dbo.module.sql`** — creates `dbo.Module` (including
   `UQ_Module_Name`, declared in that base file and applied by U-053's migration).
   Required before RoleModule because of `FK_RoleModule_Module`.

3. **`entities/role_module/sql/dbo.rolemodule.sql`** — table (with `CanViewTeam`
   column + idempotent guard), all 8 sprocs, FK constraints, and
   `UQ_RoleModule_RoleId_ModuleId`.

3b. **`entities/role_module/sql/migrations/001_rbac_join_integrity_constraints.sql`**
   — apply vehicle for an existing database: creates the three RoleModule
   constraints plus `UQ_Module_Name` on `dbo.Module` (data-guarded + self-verifying).
   Not needed on a from-scratch build that already ran steps 2–3.

4. **`scripts/migrations/time_entry_view_team.sql`** — seed grants setting
   `CanViewTeam=1` for Owner / Project Manager / Controller / Tenant Admin on
   the Time Tracking module. **Must run after step 3** because the seed updates
   `CanViewTeam`, which the base file creates.

## Superseded migration stubs

`scripts/migrations/time_entry_view_team.sql` retains header intent and SUPERSEDED
banners for the RoleModule schema and CRUD sproc sections (U-048) but no longer
carries live bodies for them. Re-running it is a no-op for those objects; it
still applies the section-2 seed grants.

TimeEntry sproc/UDF sections in the same file are superseded stubs (U-045) —
see `entities/time_entry/sql/README.md`.
