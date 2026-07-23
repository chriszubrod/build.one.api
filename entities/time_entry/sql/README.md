# TimeEntry SQL build order

## Single source of truth

`dbo.time_entry.sql` is the **single canonical source** for all 27 TimeEntry /
TimeLog / TimeEntryStatus stored procedures. No migration may redefine them —
change the base file and apply it. Enforced by `tests/test_sproc_single_source.py`
(parametrized per entity; the `time_entry` row is this file's guard). Duplicate
bodies that drift from the base file caused the 2026-07-15 outage (SQL 8144,
cross-user payroll exposure risk).

**U-125 (2026-07-23):** eight sprocs formerly sole-sourced in migrations 001–013
(`AggregateTimeEntryOnSubmit`, `IsTimeEntryDownstreamLocked`,
`ReadTimeEntryBilledLineage`, `StampTimeEntryReview`,
`ReadDistinctProjectIdsByTimeEntryIds`, `ReadTimeEntriesForDigestByWorkDate`,
`ReadTimeLogsByTimeEntryIds`, `ReadCurrentTimeEntryStatusesByTimeEntryIds`) are
now homed in the base file using live prod bodies captured via `sys.sql_modules`.
Those migration files are SUPERSEDED stubs. `CountMsOutboxByEntity` (introduced
in migration 011 for digest idempotency) is homed in
`integrations/ms/outbox/sql/ms.outbox.sql` and guarded via `SINGLE_SOURCE_SPROCS`.

The `dbo.UserCanAccessTimeEntry` UDF is **not** in this file — it moved to
`shared/sql/dbo.access_udfs.sql` in U-051, which is the canonical home for the
whole `dbo.UserCanAccess*` family. See `shared/sql/README.md`.

## From-scratch build order

1. **`entities/time_entry/sql/dbo.time_entry.sql`** — tables, indexes, all 27
   sprocs. The RBAC-scoped sprocs reference `dbo.UserProject` at runtime, but
   SQL Server defers name resolution for stored procedures, so UserProject is
   not a CREATE-time prerequisite for this file. UserProject ordering for the
   access UDFs lives in `shared/sql/README.md`.

2. **`shared/sql/dbo.access_udfs.sql`** — must run *after* step 1, because
   `dbo.UserCanAccessTimeEntry` schema-binds `dbo.TimeEntry` and `dbo.TimeLog`.
   **Do not run it here, though** — it also schema-binds the project /
   user_project / bill / bill_credit / expense packages' tables, and needs
   `CreatedByUserId` (added by `scripts/migrations/gap2_created_by_user_id.sql`,
   which itself has broad prerequisites) already present.
   `shared/sql/README.md` is the authority for its full prerequisite list and is
   where it is sequenced; this entry only records that it follows this file.

3. **Schema-only migrations** (no sproc bodies in the base file):
   - `003_align_module_route.sql`
   - `004_cascade_delete_status.sql`
   - `005_add_review_columns.sql`
   - `007_2026_05_28_add_source_time_entry_id_to_line_items.sql`

4. **`scripts/migrations/time_log_update_guards_and_unique_indexes.sql`** —
   unique indexes only (`UX_TimeLog_TimeEntry_ClockIn`,
   `UX_TimeEntry_UserId_WorkDate`). Sproc bodies in this file are superseded
   stubs (U-045).

5. **`entities/role_module/sql/dbo.rolemodule.sql`** — creates the
   `RoleModule.CanViewTeam` column that step 6's seed updates. Canonical for the
   RoleModule table + all 8 of its sprocs since U-048; see
   `entities/role_module/sql/README.md`. **Required before step 6** — without it
   the seed fails with `Invalid column name 'CanViewTeam'`.

6. **`scripts/migrations/time_entry_view_team.sql`** — RoleModule `CanViewTeam`
   **seed grants only**. Its TimeEntry sproc/UDF bodies are superseded stubs
   (U-045); its RoleModule column + CRUD sproc bodies are superseded stubs
   (U-048, now canonical in step 5).

## Superseded migration stubs

These files retain header intent and SUPERSEDED banners but no longer carry live
bodies for sprocs defined in the base file (or, for 011,
`integrations/ms/outbox/sql/ms.outbox.sql`). Re-running them is a no-op for
those sprocs:

- `001_2026_05_27_aggregate_on_submit.sql`
- `002_2026_05_27_aggregate_contract_labor_lineage.sql`
- `003_2026_05_27_downstream_lock_check.sql`
- `004_2026_05_28_billed_lineage.sql`
- `006_stamp_review_sproc.sql`
- `008_2026_05_28_aggregate_with_line_items.sql`
- `009_2026_06_03_aggregate_parent_per_time_entry.sql`
- `010_2026_06_03_distinct_project_ids_by_time_entry_ids.sql`
- `011_2026_06_16_time_entry_digest.sql`
- `012_2026_06_16_read_time_logs_by_time_entry_ids.sql`
- `013_2026_06_16_read_current_time_entry_statuses_by_ids.sql`
- `001_phase3_scope_by_user.sql`
- `002_remove_legacy_actor_bypass.sql`
- `014_2026_07_01_read_time_entries_sort_by_worker.sql`
- `015_status_read_id_tiebreak.sql`
- `016_read_time_entries_sort_by_worker.sql`
- `scripts/migrations/time_entry_view_team.sql` — TimeEntry sproc + UDF sections
  (U-045) **and** the RoleModule column + CRUD sproc sections (U-048, canonical
  in `entities/role_module/sql/dbo.rolemodule.sql`); the only live content left
  is the RoleModule `CanViewTeam` seed grants
- `scripts/migrations/time_log_update_guards_and_unique_indexes.sql` — Update*
  sproc sections only; still carries live unique indexes
  (`UX_TimeLog_TimeEntry_ClockIn`, `UX_TimeEntry_UserId_WorkDate`)

## Resolved issues

- **U-049 — RESOLVED (U-125, 2026-07-23).** `AggregateTimeEntryOnSubmit` is
  canonical in `dbo.time_entry.sql` (live prod body). Migrations 001/002/008/009
  are stubs; 001/002/008 bodies were stale vs prod.

- **U-048 — RESOLVED 2026-07-16.** `entities/role_module/sql/dbo.rolemodule.sql`
  is now canonical for the `CanViewTeam` column + all 7 CanViewTeam-aware CRUD
  sprocs (verified byte-equivalent to live prod), so re-running it no longer
  reverts CanViewTeam round-tripping. `time_entry_view_team.sql` keeps only its
  seed grants — hence steps 5 → 6 above.

- **U-051 — RESOLVED 2026-07-16.** The `dbo.UserCanAccess{TimeEntry,Project,Bill,
  BillCredit,Expense}` UDF family now has a single canonical home in
  `shared/sql/dbo.access_udfs.sql`; the three `gap1`/`gap2`/`gap3` migrations
  are superseded stubs. See `shared/sql/README.md`.

Note this directory has **four colliding numeric prefixes** (001, 002, 003, 004
each name two different files), so always cite migrations by full filename.
