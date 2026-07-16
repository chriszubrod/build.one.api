# TimeEntry SQL build order

## Single source of truth

`dbo.time_entry.sql` is the **single canonical source** for all 19 TimeEntry /
TimeLog / TimeEntryStatus stored procedures plus the `dbo.UserCanAccessTimeEntry`
UDF. No migration may redefine them — change the base file and apply it.
Enforced by `tests/test_time_entry_sproc_single_source.py`. Duplicate bodies that
drift from the base file caused the 2026-07-15 outage (SQL 8144, cross-user
payroll exposure risk).

## From-scratch build order

1. **`entities/user_project/sql/dbo.userproject.sql`** — creates `dbo.UserProject`.
   Required before the TimeEntry base file because `dbo.UserCanAccessTimeEntry`
   is `WITH SCHEMABINDING` and binds to `dbo.UserProject`.

2. **`entities/time_entry/sql/dbo.time_entry.sql`** — tables, indexes, UDF, all
   19 sprocs.

3. **Sole-source sproc migrations** (each is the only home of its sproc, so each
   still carries a live body — this is correct and expected):
   - `003_2026_05_27_downstream_lock_check.sql` (`IsTimeEntryDownstreamLocked`)
   - `004_2026_05_28_billed_lineage.sql` (`ReadTimeEntryBilledLineage`)
   - `005_add_review_columns.sql` (schema)
   - `006_stamp_review_sproc.sql` (`StampTimeEntryReview`)
   - `007_2026_05_28_add_source_time_entry_id_to_line_items.sql` (schema)
   - `009_2026_06_03_aggregate_parent_per_time_entry.sql` (`AggregateTimeEntryOnSubmit`
     — latest of a 4-file chain; see known issue below)
   - `010_2026_06_03_distinct_project_ids_by_time_entry_ids.sql`
   - `011_2026_06_16_time_entry_digest.sql`
   - `012_2026_06_16_read_time_logs_by_time_entry_ids.sql`
   - `013_2026_06_16_read_current_time_entry_statuses_by_ids.sql`

   Schema-only migrations (no sproc bodies in the base file):
   - `003_align_module_route.sql`
   - `004_cascade_delete_status.sql`

4. **`scripts/migrations/time_log_update_guards_and_unique_indexes.sql`** —
   unique indexes only (`UX_TimeLog_TimeEntry_ClockIn`,
   `UX_TimeEntry_UserId_WorkDate`). Sproc bodies in this file are superseded
   stubs (U-045).

5. **`scripts/migrations/time_entry_view_team.sql`** — RoleModule `CanViewTeam`
   column + seed + RoleModule CRUD sprocs. TimeEntry sproc/UDF bodies in this
   file are superseded stubs (U-045).

## Superseded migration stubs

These files retain header intent and SUPERSEDED banners but no longer carry live
bodies for the 16 RBAC-scoped read/mutation sprocs defined in the base file.
Re-running them is a no-op for those sprocs:

- `001_phase3_scope_by_user.sql`
- `002_remove_legacy_actor_bypass.sql`
- `014_2026_07_01_read_time_entries_sort_by_worker.sql`
- `015_status_read_id_tiebreak.sql`
- `016_read_time_entries_sort_by_worker.sql`
- `scripts/migrations/time_entry_view_team.sql` — TimeEntry sproc + UDF sections
  only; still carries live RoleModule `CanViewTeam` column, seed, and CRUD sprocs
- `scripts/migrations/time_log_update_guards_and_unique_indexes.sql` — Update*
  sproc sections only; still carries live unique indexes
  (`UX_TimeLog_TimeEntry_ClockIn`, `UX_TimeEntry_UserId_WorkDate`)

## Known issues (out of scope for U-045)

Tracked in `TODO.md` — that is the canonical register; these are one-line
pointers so a reader of this build order knows why it looks the way it does.
Note this directory has **four colliding numeric prefixes** (001, 002, 003, 004
each name two different files), so always cite migrations by full filename.

- **U-049** — `AggregateTimeEntryOnSubmit` is not in the base file and has 4
  *distinct* bodies across `001_2026_05_27_aggregate_on_submit.sql`,
  `002_2026_05_27_aggregate_contract_labor_lineage.sql`,
  `008_2026_05_28_aggregate_with_line_items.sql` and
  `009_2026_06_03_aggregate_parent_per_time_entry.sql`. Unlike the superseded
  stubs above these are **not** byte-identical, so re-running an early one is a
  real revert. This is why step 3 lists only `009` (presumed-latest by number);
  confirming the live body needs a prod read, so it is not settled here.

- **U-048** — `entities/role_module/sql/dbo.rolemodule.sql` has 8 sprocs and no
  `CanViewTeam`, so re-running it reverts CanViewTeam round-tripping. This is
  why step 5's `time_entry_view_team.sql` keeps its RoleModule half.

- **U-050** — the sibling `UserCanAccess{Project,Bill,BillCredit,Expense}` UDFs
  have 3 distinct bodies each across the `gap1`/`gap2`/`gap3` migrations and no
  base-file home, and `entities/project/sql/dbo.project.sql` still carries a
  live `OR @ActorUserId IS NULL` bypass its own migration 002 removes.
