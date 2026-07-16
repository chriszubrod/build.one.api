-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-051, 2026-07-16) — UDF bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Gap 3 — removed the @ActorUserId IS NULL legacy-caller bypass so the
--   checks fail closed.
--
-- The canonical definition of these UDFs now lives in exactly ONE place:
--   shared/sql/dbo.access_udfs.sql
--
-- UDFs formerly redefined here (now canonical in the shared file):
--   dbo.UserCanAccessProject, dbo.UserCanAccessBill,
--   dbo.UserCanAccessBillCredit, dbo.UserCanAccessExpense
--
-- Re-running this file is now a no-op. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------

-- =====================================================================
-- Gap 3 (2026-05-12) — remove the `@ActorUserId IS NULL THEN 1` bypass
-- from dbo.UserCanAccess{Bill, BillCredit, Expense, Project} UDFs.
-- Replaces `gap2_user_can_access_creator_clause.sql`.
--
-- Background: Gap 1/2 included `WHEN @ActorUserId IS NULL THEN 1` so that
-- pre-rollout callers (services that hadn't yet learned to thread actor
-- context) would keep working during the staged deploy. After a leak was
-- discovered on the TimeEntry side where this bypass turned into a
-- silent "no actor → access granted" path on by-id read gates, we removed
-- it across every user-scoped access check to fail closed.
--
-- New rule for these UDFs:
--   Admin (@ActorIsSystemAdmin = 1) → 1
--   Creator (parent.CreatedByUserId = @ActorUserId) → 1
--   UserProject membership reaches the row → 1
--   Else → 0 (including NULL @ActorUserId — fail closed)
--
-- Scheduler / system callers populate `current_is_system_admin = True`
-- (via `shared/api/admin.py::_require_drain_secret`) so the
-- @ActorIsSystemAdmin = 1 branch grants access via the intended path.
--
-- Preserves the gap2 "creator clause" shape — the only delta is the
-- removal of one `WHEN @ActorUserId IS NULL` branch per UDF.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO
