# Contract Labor SQL build order

## Single source of truth

`dbo.contract_labor.sql` is the **single canonical source** for the
`dbo.ContractLabor` and `dbo.ContractLaborLineItem` table bootstraps and for all
**28** stored procedures it declares. No migration may redefine them — change the
base file and apply it. Enforced by `tests/test_sproc_single_source.py`
(whole-file guard, `ENTITY_BASE_FILES`). Duplicate bodies that drift from the base
file break net-zero with prod.

U-162 (2026-07-28) **RECONCILED** the base to the live layer. The base was
**STALE** for three sprocs:

| Sproc | What the base was missing | Live source it was reconciled from |
|---|---|---|
| `CreateContractLabor` | `@CreatedByUserId`, the `[CreatedByUserId]` INSERT column, `COALESCE(@CreatedByUserId, 17)` | `scripts/migrations/gap2_core_threading.sql` |
| `ReadContractLaborDailySummary` | Live prod body (assignment-only SELECTs; no pyodbc break — verified 2026-07-28) | LIVE prod (U-162 Gate-2 sweep) |
| `ReadContractLaborLineItemsByContractLaborId` | `SET NOCOUNT ON` + the `OUTER APPLY dbo.TimeLog` clock-in ordering | `entities/contract_labor/sql/migrations/2026_06_03_line_items_ordered_by_clockin.sql` |

`FindContractLaborForReviewerReply` differed only in comments (T-SQL identical);
the base adopted the migration's explanatory blocks. `ReadContractLaborByPublicId`,
`ReadContractLaborByNaturalKey` and `CreateContractLaborLineItem` were already
live in the base. The base also carried a **second, byte-identical**
`ReadContractLaborByPublicId` block, which U-162 collapsed — the base now defines
each sproc exactly once.

## ⚠️ Applying this file to prod

Re-applying the **pre-U-162** base file would have **reverted prod** in two ways:

1. **SQL 8145 on every contract-labor create** — the base's `CreateContractLabor`
   had no `@CreatedByUserId`, and `ContractLaborRepository.create` sends it
   unconditionally (`entities/contract_labor/persistence/repo.py`). Same param-drift
   class as the U-037 / U-089 / U-102 / U-158 incidents. **This is the financial
   hazard**: ContractLabor rows are the input to CL bill generation.
2. **`ReadContractLaborDailySummary` body drift** — the base had diverged from live
   prod (carried pointless `BEGIN TRANSACTION`/`COMMIT` on a read-only sproc). Note:
   assignment-only `SELECT @var = …` does **not** break pyodbc on prod (verified
   2026-07-28); the real pyodbc failure shape is DML followed by a row-returning
   SELECT with `fetchone()` (e.g. `UpdateContractLaborStatusByIds`).

U-164 (2026-07-28) changed three sproc bodies in this file — `SET NOCOUNT ON` on
`UpdateContractLaborStatusByIds`, `DeleteContractLaborLineItemsByContractLaborId`, and
`ReadContractLaborDailySummary`, plus removal of the read-only `BEGIN TRANSACTION`/`COMMIT`
from `ReadContractLaborDailySummary`. Apply surgically — those three `CREATE OR ALTER`
blocks only, never the whole file. *(Dated note: between the U-164 commit and the U-164 SQL
apply the base was intentionally ahead of live for those three; after apply, re-applying
this file matches prod again.)*

## ⚠️ Not the complete CL sproc set

`ReadContractLaborDistinctBillingPeriods` is a **live** contract-labor sproc
(called at `entities/contract_labor/persistence/repo.py:756`) whose only definition
is `entities/contract_labor/sql/ReadContractLaborDistinctBillingPeriods.sql` — it is
**not** in the base file. Legal and sole-homed, so no guard fires, but a
from-scratch build from the base alone yields a missing sproc. Apply that file too
(step 6 below). Folding it into the base is a tracked follow-up.

## From-scratch build order

1. **Prerequisites** — `dbo.Vendor`, `dbo.Project`, `dbo.SubCostCode`,
   `dbo.BillLineItem` must exist (FKs on `dbo.ContractLabor`), plus `dbo.TimeEntry`
   and `dbo.TimeLog` before the sprocs that join them compile.

2. **`entities/contract_labor/sql/dbo.contract_labor.sql` (first pass)** — the two
   guarded `CREATE TABLE` blocks and the `CREATE INDEX` blocks succeed, but the
   sproc batches **fail** at `CREATE PROCEDURE` time: SQL Server validates columns
   against existing tables, and neither `CreatedByUserId` nor `SourceTimeEntryId`
   is present yet. The guarded `CREATE TABLE` blocks do **not** include
   `CreatedByUserId` (added by `scripts/migrations/gap2_created_by_user_id.sql`),
   `SourceTimeEntryId` (added by the migrations in step 3), or `CompanyId`
   (phase5 migrations).

3. **Column migrations** —
   `entities/contract_labor/sql/migrations/2026_05_27_source_time_entry_id.sql`
   (adds `ContractLabor.SourceTimeEntryId` + FK + index),
   `entities/time_entry/sql/migrations/007_2026_05_28_add_source_time_entry_id_to_line_items.sql`
   (adds `ContractLaborLineItem.SourceTimeEntryId`, required by the clock-in
   ordering in `ReadContractLaborLineItemsByContractLaborId`), and
   `entities/contract_labor/sql/add_contract_labor_line_item_bill_line_item_id.sql`
   (adds `ContractLaborLineItem.BillLineItemId` + FK).

4. **`scripts/migrations/gap2_created_by_user_id.sql`** — adds `CreatedByUserId`.

5. **`entities/contract_labor/sql/dbo.contract_labor.sql` (second pass)** —
   idempotent `CREATE OR ALTER`; all 28 sprocs apply.

6. **`entities/contract_labor/sql/ReadContractLaborDistinctBillingPeriods.sql`** —
   the one CL sproc not homed in the base (see the section above).

7. **`entities/contract_labor/sql/migrations/2026_07_02_unify_labor_status_vocab.sql`**
   — migrates the status vocab (`pending_review`→`draft`, `ready`→`approved`,
   `billed`→`completed`) and re-points the `Status` default to `'draft'`.
   ⚠️ The base's guarded `CREATE TABLE` still declares
   `DEFAULT 'pending_review'`, and `CreateContractLabor` still defaults
   `@Status = 'pending_review'`, as does the Python layer. Because the
   `CREATE TABLE` is `IF OBJECT_ID(…) IS NULL`-guarded this is a **no-op against
   prod**, but from scratch it leaves the app and DB on different vocabularies.
   Tracked as its own follow-up unit — deliberately NOT changed by U-162, which
   was behavior-frozen.

8. **`scripts/migrations/phase5_company_id_*.sql`** — adds `CompanyId`
   (column → `DEFAULT (1)` → `NOT NULL` flip). ⚠️ Steps 1–7 get the **sprocs** to
   apply, not full schema parity: prod's tables carry `CompanyId BIGINT NOT NULL`
   and the guarded `CREATE TABLE` never adds it. Skip this and the table is
   silently divergent from prod. Tracked as the campaign-wide "base files are not
   self-contained from scratch" residual.

The `CREATE INDEX` blocks and the `IsOverhead` column-add at the top of the base
file are `IF NOT EXISTS`-guarded and are no-ops against a prod that already has them.

## Superseded migration stubs

U-162 neutralized **7 duplicate sproc bodies across 6 carrier files**. Re-running
any of these is now a no-op for the named object:

| File | Stubbed sproc(s) |
|---|---|
| `scripts/migrations/gap2_core_threading.sql` | `CreateContractLabor`, `CreateContractLaborLineItem` |
| `scripts/migrations/2026_05_27_find_contract_labor_for_reviewer_reply.sql` | `FindContractLaborForReviewerReply` (the `dbo.ContractLaborNotification` table DDL + backfill in that file are **still live**) |
| `entities/contract_labor/sql/ReadContractLaborByNaturalKey.sql` | `ReadContractLaborByNaturalKey` (whole-file stub) |
| `entities/contract_labor/sql/ReadContractLaborDailySummary.sql` | `ReadContractLaborDailySummary` (whole-file stub) |
| `entities/contract_labor/sql/migrations/2026_05_28_read_source_time_entry_public_id.sql` | `ReadContractLaborByPublicId` |
| `entities/contract_labor/sql/migrations/2026_06_03_line_items_ordered_by_clockin.sql` | `ReadContractLaborLineItemsByContractLaborId` |

`entities/contract_labor/sql/migrations/2026_06_09_update_contract_labor_aggregates.sql`
carries an earlier SUPERSEDED (U-126) stub for `UpdateContractLaborAggregates`.
