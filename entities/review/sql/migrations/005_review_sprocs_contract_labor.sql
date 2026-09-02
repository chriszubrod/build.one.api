-- =============================================================================
-- 2026-05-28 — Review sprocs + view: contract_labor parent support.
--
-- Extends the view to include ContractLaborId, the CreateReview sproc to
-- accept it, and adds ReadReviewsByContractLaborId / ReadCurrentReview-
-- ByContractLaborId / DeleteReviewsByContractLaborId — same shape as the
-- existing Bill/Expense/BillCredit/Invoice triple.
--
-- Run AFTER 003_add_contract_labor_parent.sql (which adds the column).
--
-- Idempotent — all CREATE OR ALTER.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-357b, 2026-09-01) — view + sproc bodies removed, NOT the intent.
-- Canonical definition now lives in exactly ONE place:
--   entities/review/sql/dbo.review.sql
-- Objects formerly redefined here (now canonical in the base file):
--   dbo.vw_Review (projects [ContractLaborId]) · dbo.CreateReview (@ContractLaborId BIGINT = NULL)
-- Re-running this file is now a no-op for these objects. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is the single-source hazard
-- (U-037: a stale redefinition dropped live sproc params -> SQL 8144 outage).
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-126, 2026-07-23) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Contract-labor parent review read/delete sprocs matching Bill/Expense shape.
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/review/sql/dbo.review.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.ReadReviewsByContractLaborId
--   dbo.ReadCurrentReviewByContractLaborId
--   dbo.DeleteReviewsByContractLaborId
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------


PRINT 'migrations/005 is superseded — no-op (see the banners above).';
