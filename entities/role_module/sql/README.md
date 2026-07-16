# RoleModule SQL build order

## Single source of truth

`dbo.rolemodule.sql` is the **single canonical source** for the `dbo.RoleModule`
table (including the `CanViewTeam` column) and all 8 RoleModule stored
procedures. No migration may redefine them — change the base file and apply it.
Enforced by `tests/test_sproc_single_source.py`. Duplicate bodies that drift from
the base file break net-zero with prod.

`DeleteRoleModuleById` deliberately omits `CanViewTeam` from its `OUTPUT DELETED`
list to stay byte-identical with the deployed prod body.

## ⚠️ Applying this file to prod is NOT currently a no-op

All 8 sproc bodies here were verified byte-equivalent to live prod (read-only
`sys.sql_modules` read, 2026-07-16), so **the sprocs re-apply cleanly**. The
`CanViewTeam` ALTER is `IF NOT EXISTS`-guarded and inert (prod has the column).

**But prod has none of this file's constraints** — `sys.foreign_keys` for
`dbo.RoleModule` returns zero rows and the only constraint present is the PK, so
applying this file would **CREATE** `FK_RoleModule_Role`, `FK_RoleModule_Module`
and `UQ_RoleModule_RoleId_ModuleId` for the first time. That is a real schema
change, not a no-op. Data supported all three as of 2026-07-16 (0 duplicate
`(RoleId, ModuleId)` pairs, 0 orphan `RoleId`, 0 orphan `ModuleId` over 136 rows)
— **re-verify those three counts immediately before applying**, and treat the
apply as its own gated decision. Tracked in `TODO.md`.

Why they were never applied: `FK_RoleModule_Role` had no `GO` before it, so it
was swallowed into `DeleteRoleModuleById`'s body — a `CREATE PROCEDURE` body runs
to the end of the **batch**, not to its matching `END`. U-048 added the missing
`GO`. Watch for that shape when converting other entities.

## From-scratch build order

1. **`entities/role/sql/dbo.role.sql`** — creates `dbo.Role`. Required before
   RoleModule because of `FK_RoleModule_Role`.

2. **`entities/module/sql/dbo.module.sql`** — creates `dbo.Module`.
   Required before RoleModule because of `FK_RoleModule_Module`.

3. **`entities/role_module/sql/dbo.rolemodule.sql`** — table (with `CanViewTeam`
   column + idempotent guard), all 8 sprocs, FK constraints, and
   `UQ_RoleModule_RoleId_ModuleId`.

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
