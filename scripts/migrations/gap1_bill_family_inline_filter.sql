-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Gap 1 perf fixup — replace UserCanAccessBill/BillCredit/Expense UDF
--   calls with inline EXISTS clauses on the Bill/BillCredit/Expense list
--   + paginated + count sprocs.
--
--   Why: the UDFs wrap an EXISTS in CONVERT(BIT, CASE) which appears to
--   prevent SQL Server's scalar UDF inlining (Froid). On 18K-row Bill
--   counts the UDF is called per-row → minutes-long queries. Inline
--   EXISTS lets the optimizer push it into a semi-join.
--
--   Idempotent (CREATE OR ALTER). Safe to re-run.
--   The UDFs themselves stay in place (other future callers may use them);
--   this migration just removes them from the hot list/paginated/count path.
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
-- DANGER (motivated U-100): bodies here carry stale pre-UDF inline EXISTS
-- scoping (superseded by UserCanAccess* in the canonical base files), Bill
-- copies omit SourceEmailMessageId from projections, and v1 carries the
-- legacy `@ActorUserId IS NULL` fail-open bypass. Re-applying would unscope
-- or regress prod list reads.
-- ---------------------------------------------------------------------------

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definitions live in entity base files (bill, bill_credit, expense).';
