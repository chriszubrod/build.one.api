-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-158, 2026-07-28) — WHOLE FILE. Every sproc body this file once
-- carried is now a pointer stub; applying it modifies ZERO database objects.
--
-- The scoped bodies live in their entity base files:
--   Bill / Expense / ContractLabor  — U-089, entities/{bill,expense,contract_labor}/sql/
--   BillCredit                      — U-100, entities/bill_credit/sql/dbo.bill_credit.sql
--   Invoice                         — U-158, entities/invoice/sql/dbo.invoice.sql
-- Change the base file and apply it; do NOT reintroduce a body here.
-- Scoping is guarded by tests/test_list_sproc_scoping.py.
--
-- Original intent of this file (preserved for lineage):
--   Gap 1 — List-path read sprocs scoped by UserProject membership.
--
--   Per Q1.1 + Q1.2 + Q1.3: enforces UserProject scoping on the list /
--   paginated / count read sprocs across the 5 transactional entities
--   whose direct lookups (by_id / by_public_id / by_other_keys) are NOT
--   yet scoped — those land in a follow-up tightening pass.
--
--   Project's full read surface (4 sprocs) is already scoped via
--   entities/project/sql/migrations/001_gap1_scope_by_user_project.sql.
--
--   Filter: each affected sproc gains
--       @ActorUserId BIGINT = NULL,
--       @ActorIsSystemAdmin BIT = NULL
--   and an `AND dbo.UserCanAccess<Entity>(...) = 1` clause. NULL
--   @ActorUserId bypasses (back-compat during deploy).
--
--   Idempotent (CREATE OR ALTER). Safe to re-run.
-- ---------------------------------------------------------------------------

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- =====================================================================
-- Bill — line items carry ProjectId
-- =====================================================================

-- SUPERSEDED (U-089): dbo.ReadBills single-sourced in entities/bill/sql/dbo.bill.sql.
GO

-- SUPERSEDED (U-089): dbo.ReadBillsPaginated single-sourced in entities/bill/sql/dbo.bill.sql.
GO

-- SUPERSEDED (U-089): dbo.CountBills single-sourced in entities/bill/sql/dbo.bill.sql.
GO

-- =====================================================================
-- BillCredit — line items carry ProjectId
-- DANGER (U-100): the three UserCanAccessBillCredit-UDF bodies this section
-- held were retired UNAPPLIED — sys.sql_modules (2026-07-21) proved prod
-- never ran them; live prod runs the gap1-v3 inline-scoped form, which now
-- lives (base==live verified) in entities/bill_credit/sql/dbo.bill_credit.sql.
-- Re-introducing any body here would fork that single source again; an
-- unscoped body would unscope prod BillCredit lists → SQL 8145/500.
-- =====================================================================

-- SUPERSEDED (U-100): dbo.ReadBillCredits single-sourced in entities/bill_credit/sql/dbo.bill_credit.sql.
GO

-- SUPERSEDED (U-100): dbo.ReadBillCreditsPaginated single-sourced in entities/bill_credit/sql/dbo.bill_credit.sql.
GO

-- SUPERSEDED (U-100): dbo.CountBillCredits single-sourced in entities/bill_credit/sql/dbo.bill_credit.sql.
GO

-- =====================================================================
-- Expense — line items carry ProjectId
-- =====================================================================

-- SUPERSEDED (U-089): dbo.ReadExpenses single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-089): dbo.ReadExpensesPaginated single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-089): dbo.CountExpenses single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- =====================================================================
-- Invoice — direct ProjectId on parent
-- =====================================================================

-- SUPERSEDED (U-158): dbo.ReadInvoices single-sourced in entities/invoice/sql/dbo.invoice.sql.
GO

-- SUPERSEDED (U-158): dbo.ReadInvoicesPaginated single-sourced in entities/invoice/sql/dbo.invoice.sql.
GO

-- SUPERSEDED (U-158): dbo.CountInvoices single-sourced in entities/invoice/sql/dbo.invoice.sql.
GO

-- =====================================================================
-- ContractLabor — direct ProjectId on parent
-- =====================================================================

-- SUPERSEDED (U-089): dbo.ReadContractLabors single-sourced in entities/contract_labor/sql/dbo.contract_labor.sql.
GO

-- SUPERSEDED (U-089): dbo.ReadContractLaborsPaginated single-sourced in entities/contract_labor/sql/dbo.contract_labor.sql.
GO

-- SUPERSEDED (U-089): dbo.CountContractLabors single-sourced in entities/contract_labor/sql/dbo.contract_labor.sql.
GO

PRINT 'SUPERSEDED (U-158): no sprocs applied; canonical scoped definitions live in the bill / bill_credit / expense / invoice / contract_labor entity base files.';
