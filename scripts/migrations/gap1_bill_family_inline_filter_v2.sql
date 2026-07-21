-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Gap 1 perf fixup v2 — IN-subquery instead of correlated EXISTS for
--   Bill / BillCredit / Expense list paths.
--
--   Why: even inline correlated EXISTS was 2+ min on Bill non-admin
--   queries. SQL Server was re-evaluating the line-item join per Bill
--   row instead of materializing the accessible BillId set once. Switching
--   to a non-correlated IN-subquery lets the optimizer build the set once
--   and hash-semi-join it against Bill.Id.
--
--   Also fixes the correctness gap: bills with no line items (or
--   line items with NULL ProjectId) are now visible to the user who
--   CREATED them (Bill.CreatedByUserId = @ActorUserId) — covers the
--   empty-draft case that the EXISTS-via-line-items model can't reach.
--
--   Idempotent. Safe to re-run.
--
-- The canonical definitions now live in exactly ONE place each:
--   ReadBills, ReadBillsPaginated, CountBills
--     → entities/bill/sql/dbo.bill.sql
--   ReadBillCredits, ReadBillCreditsPaginated, CountBillCredits
--     → entities/bill_credit/sql/dbo.bill_credit.sql
--   ReadExpenses, ReadExpensesPaginated, CountExpenses
--     → entities/expense/sql/dbo.expense.sql
--
-- Re-running this file is now a no-op. Do NOT reintroduce bodies here.
--
-- DANGER (motivated U-100): bodies here carry stale pre-UDF IN-subquery
-- scoping (superseded by UserCanAccess* in the canonical base files), Bill
-- copies omit SourceEmailMessageId from projections, and v2 carries the
-- legacy `@ActorUserId IS NULL` fail-open bypass. Re-applying would unscope
-- or regress prod list reads.
-- ---------------------------------------------------------------------------

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definitions live in entity base files (bill, bill_credit, expense).';
