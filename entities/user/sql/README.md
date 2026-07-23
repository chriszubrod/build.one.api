# User SQL build order

## Single source of truth

`dbo.user.sql` is the **single canonical source** for the `dbo.User` table and all of its
stored procedures. No migration may redefine them — change the base file and apply it.
Enforced by `tests/test_sproc_single_source.py`. Duplicate bodies that drift from the
base file break net-zero with prod.

The sprocs: `CreateUser`, `ReadUsers`, `ReadUserById`, `ReadUserByPublicId`,
`ReadUserByFirstname`, `ReadUserByLastname`, `UpdateUserById`, `DeleteUserById`,
`ReadWorkers`, `SetUserLastCompanyId`, `UpdateUserWorkerLink`.

Eight sproc bodies in the base file were stale (pre–Phase 0 read shape). They were
reconciled verbatim to the newest migration layers under U-131 (2026-07-23): the three
mutation sprocs from `migrations/003_phase1_attribution_sprocs.sql`, the five read
sprocs from `migrations/005_2026_05_27_worker_links.sql` (which already incorporated
Phase 0 extended columns and Phase 4 `@IncludeAgents`). `ReadWorkers`,
`SetUserLastCompanyId`, and `UpdateUserWorkerLink` were homed earlier (U-126).

## ⚠️ Applying this file to prod

All eight reconciled sproc bodies were verified equivalent to live prod (read-only
`sys.sql_modules` sweep, comment/whitespace-normalized, 2026-07-23, U-131 Gate-2), so
**the sprocs re-apply cleanly**. After any future edit to a sproc here, re-verify
base==live the same way before treating a re-apply as a no-op — only prod is truth.

Column/index DDL in this file (including worker-link `EmployeeId` / `VendorId`, filtered
unique indexes, and `IX_User_PublicId`) is idempotent via `IF NOT EXISTS` / `IF COL_LENGTH`
guards, so re-applying it no-ops on an environment that is already current.

## From-scratch build order

1. **Prerequisites** — `dbo.Employee` and `dbo.Vendor` tables must exist before FK
   constraints on `User.EmployeeId` / `User.VendorId` can be created (guards skip FKs
   when parent tables are missing).

2. **`entities/user/sql/dbo.user.sql`** — table, idempotent column adds, FKs, filtered
   unique indexes, all sprocs, and `IX_User_PublicId`.

3. **`migrations/001_phase0_access_control.sql`** — adds `IsSystemAdmin`, `IsAgent`,
   `LastCompanyId`, `CreatedByUserId`, and `ModifiedByUserId`; run before relying on
   the read/mutation sprocs in the base file. On prod these columns pre-exist; the
   base sprocs assume them.

## Superseded migration stubs

`migrations/002_phase0_read_sprocs_extended.sql`,
`migrations/003_phase1_attribution_sprocs.sql`,
`migrations/004_phase4_include_agents_filter.sql` (all U-131),
`migrations/2026_06_10_read_workers.sql` (U-126), and the read-sproc section of
`migrations/005_2026_05_27_worker_links.sql` (U-131) carry SUPERSEDED banners and no
live sproc bodies — see each file's banner.

`migrations/005_2026_05_27_worker_links.sql` also retains its worker-link DDL (columns,
FKs, filtered unique indexes) as a frozen historical layer. The base file carries
equivalent guarded DDL — **edit the base file, never the migration** — the migration's
DDL stays only as a faithful record of what shipped.
