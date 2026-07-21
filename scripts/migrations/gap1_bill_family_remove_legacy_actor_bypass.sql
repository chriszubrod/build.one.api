-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Gap 1 follow-up (2026-05-12) — remove the `@ActorUserId IS NULL`
--   legacy-caller bypass from Bill / BillCredit / Expense list and
--   by-id read sprocs. Replaces `gap1_bill_family_inline_filter_v2.sql`.
--
--   Background: gap1 v2 included `OR @ActorUserId IS NULL` so that pre-
--   Gap-1 callers (services that hadn't yet learned to thread actor
--   context) would keep working during the staged deploy. After a leak
--   was discovered on the TimeEntry side where this bypass turned into
--   a silent "no actor → show everything" path, we removed it here too
--   to fail closed across the entire user-scoped read surface.
--
--   Scheduler / system callers (X-Drain-Secret-gated admin endpoints
--   in `shared/api/admin.py::_require_drain_secret`) now explicitly
--   set `current_is_system_admin = True` so the sproc's
--   @ActorIsSystemAdmin = 1 clause grants them all-user visibility
--   via the intended path. Outbox drain / QBO sync / reconciliation
--   depend on this; they read across users by design.
--
--   Preserves the v2 perf shape (IN-subquery + CreatedByUserId clause).
--   Idempotent (CREATE OR ALTER). Safe to re-run.
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
-- DANGER (motivated U-100): the Bill and Expense bodies here were STALE —
-- pre-UDF IN-subquery scoping (prod runs the UserCanAccess* form) and the
-- Bill copies omit SourceEmailMessageId from projections; re-applying them
-- would regress prod Bill/Expense list reads. The BillCredit trio was the
-- opposite case: this file's copies WERE the live prod text (sys.sql_modules
-- verified 2026-07-21) and were moved byte-exact into dbo.bill_credit.sql —
-- their canonical home now; the UDF-form copies in gap1_list_sprocs_scoped
-- were retired unapplied.
-- ---------------------------------------------------------------------------

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definitions live in entity base files (bill, bill_credit, expense).';
