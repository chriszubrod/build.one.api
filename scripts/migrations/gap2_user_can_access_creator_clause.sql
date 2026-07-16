-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-051, 2026-07-16) — UDF bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Gap 2 — added the "creator can access their own row" clause to the four
--   UserCanAccess UDFs.
--
-- The canonical definition of these UDFs now lives in exactly ONE place:
--   shared/sql/dbo.access_udfs.sql
--
-- UDFs formerly redefined here (now canonical in the shared file):
--   dbo.UserCanAccessProject, dbo.UserCanAccessBill,
--   dbo.UserCanAccessBillCredit, dbo.UserCanAccessExpense
--
-- Re-running this file is now a no-op. Do NOT reintroduce a body here.
--
-- DANGER (motivated U-051): each of these four UDFs had THREE DIVERGENT
-- bodies — one in each of gap1, gap2 and gap3. The body THIS file carried
-- (like gap1's) included a "WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)"
-- fail-open bypass branch, which gap3 deliberately removed. Re-running gap2
-- would therefore have reverted the gap3 bypass removal, silently reopening a
-- fail-open RBAC hole on Bill / BillCredit / Expense / Project by-id reads.
-- That is what this stub prevents.
--
-- NOTE: the prose below is the ORIGINAL header, preserved verbatim for
-- lineage. For current UDF behavior read shared/sql/dbo.access_udfs.sql.
-- ---------------------------------------------------------------------------

-- =====================================================================
-- Gap 2 follow-up — extend dbo.UserCanAccess{Bill, BillCredit, Expense, Project}
-- UDFs with a "creator can access their own row" clause (matches the
-- list-path filter shape from Gap 1 v3).
--
-- Why: Without this, a non-admin who creates a parent row that has not
-- yet had any child line items attached gets EntityNotAccessibleError on
-- subsequent reads (UserProject join finds no line items, so EXISTS=false).
-- That made BillService.create() fail for non-admin actors during the
-- auto-line-item-attach flow that runs in the same request as the Bill
-- INSERT (the new Bill has no line items yet).
--
-- Also closes the "Gap 1 empty-bill edge case" tracked in TODO.md —
-- empty drafts created by a user remain visible to that user.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO
