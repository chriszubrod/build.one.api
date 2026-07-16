# Access UDF SQL build order

## Single source of truth

`dbo.access_udfs.sql` is the **single canonical source** for all five
`dbo.UserCanAccess*` UDFs:

- `UserCanAccessTimeEntry`
- `UserCanAccessProject`
- `UserCanAccessBill`
- `UserCanAccessBillCredit`
- `UserCanAccessExpense`

No migration and no entity base file may redefine them — change this file and
apply it. Enforced by `tests/test_sproc_single_source.py`.

## Why a shared home, not per-entity base files

For the **four creator-clause UDFs** (`Project`, `Bill`, `BillCredit`,
`Expense`) an entity-local home is not a style preference — it is structurally
impossible. Each is `WITH SCHEMABINDING` against a table from **another** entity
package, and that package's own file has a foreign key pointing back, so homing
the UDF in its entity base file makes a from-scratch build **cycle**:

- `UserCanAccessProject` binds `dbo.UserProject`, but
  `entities/user_project/sql/dbo.userproject.sql` has `FK_UserProject_Project`
  referencing `dbo.Project`.
- `UserCanAccessBill` binds `dbo.BillLineItem`, but
  `entities/bill_line_item/sql/dbo.bill_line_item.sql` has
  `FK_BillLineItem_Bill` referencing `dbo.Bill`.

Same shape for BillCredit and Expense. A cross-entity family needs a
cross-entity home. This mirrors the Python layer: `shared/access.py` is the
consumer of exactly these UDFs.

**`UserCanAccessTimeEntry` is the exception that proves it.** It has **no**
cycle — it binds `dbo.TimeEntry` + `dbo.TimeLog` (created by its own base file)
plus `dbo.UserProject`, and `dbo.userproject.sql` has no FK back to TimeEntry.
That is a plain ordering dependency, which is why U-045 could home it in
`entities/time_entry/sql/dbo.time_entry.sql` and it worked. It lives here for a
different reason: so the family has **one** canonical location under **one**
guard, rather than four UDFs in one place and a fifth somewhere else.

## Prerequisites

This is **what this file requires**, not a whole-repo build recipe. There is no
from-scratch build runner here (`scripts/run_sql.py` takes one file at a time),
so no ordering below is claimed to be a complete, tested sequence — only these
stated dependencies are verified.

Every UDF here is `WITH SCHEMABINDING`, and **SCHEMABINDING binds at the
_column_ level, not just the table level**. Both must already exist:

**1 — the ten bound tables.** `Project` + `UserProject`; `Bill` +
`BillLineItem`; `BillCredit` + `BillCreditLineItem`; `Expense` +
`ExpenseLineItem`; `TimeEntry` + `TimeLog`. Created by:

- `entities/project/sql/dbo.project.sql`
- `entities/user_project/sql/dbo.userproject.sql` — FKs to Project, so it follows it
- `entities/bill/sql/dbo.bill.sql` → `entities/bill_line_item/sql/dbo.bill_line_item.sql`
- `entities/bill_credit/sql/dbo.bill_credit.sql` → `entities/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql`
- `entities/expense/sql/dbo.expense.sql` → `entities/expense_line_item/sql/dbo.expense_line_item.sql`
- `entities/time_entry/sql/dbo.time_entry.sql` — `TimeEntry` + `TimeLog`

**2 — the bound `CreatedByUserId` column** on `Project` / `Bill` / `BillCredit` /
`Expense`. Those four base `CREATE TABLE` blocks do **not** declare it;
`scripts/migrations/gap2_created_by_user_id.sql` adds it (+ an FK to `dbo.User`),
and `scripts/migrations/gap2_created_by_user_id_finalize.sql` backfills it,
defaults it to `17` and flips it `NOT NULL` — the shape prod runs. Without the
column this file fails with `Invalid column name 'CreatedByUserId'`.
*`UserCanAccessTimeEntry` is unaffected — it binds only base-declared columns
(`TimeEntry.UserId`, `TimeLog.ProjectId`, `UserProject.*`).*

> ⚠️ **`gap2_created_by_user_id.sql` is not a narrow prerequisite you can slot
> in right after the entity files listed above.** It `ALTER`s tables well beyond
> this UDF family's four (`Invoice`, `ContractLabor`, `Vendor`, `Customer`,
> `Review`, `Email*`, …) and adds an FK to `dbo.User` for each. Its guards test
> for the **column** (`sys.columns … OBJECT_ID('dbo.Invoice')`), **not** the
> table — so if a table is absent, `OBJECT_ID` returns `NULL`, the guard passes
> anyway, and the `ALTER TABLE` runs and fails. In practice this file therefore
> lands **late in a full build**, after the base sweep and gap2 — not after the
> subset above. Sequencing the whole repo is out of scope for this README.

## Callers (must run after this file)

SQL Server does **not** apply deferred name resolution to scalar UDFs — a
`CREATE PROCEDURE` whose body calls a missing scalar UDF fails at create time.
These files call the UDFs in sproc bodies and therefore depend on this file:

- **`entities/budget/sql/dbo.budget.sql`** and
  **`entities/budget/sql/dbo.budget_variance.sql`** — call
  `dbo.UserCanAccessProject`.
- **`scripts/migrations/gap1_list_sprocs_scoped.sql`** — calls
  `UserCanAccessBill` / `BillCredit` / `Expense` / `Project`.

This dependency already existed but was implicit — budget depended on the gap1
migration having run. Naming this file makes it explicit.

## Superseded stubs

These migration files no longer carry UDF bodies (U-051):

- `scripts/migrations/gap1_user_project_access_helpers.sql`
- `scripts/migrations/gap2_user_can_access_creator_clause.sql`
- `scripts/migrations/gap3_user_can_access_remove_legacy_actor_bypass.sql`

## UserCanAccessTimeEntry relocation

`UserCanAccessTimeEntry` moved here from `entities/time_entry/sql/dbo.time_entry.sql`
in U-051. It has no SQL callers (its consumer is `shared/access.py` at runtime),
so the move is build-order safe.
