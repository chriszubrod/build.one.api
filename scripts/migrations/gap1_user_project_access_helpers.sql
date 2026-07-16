-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-051, 2026-07-16) — UDF bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Gap 1 — the original four UserProject access-check helper UDFs.
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
-- (like gap2's) included a "WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)"
-- fail-open bypass branch, which gap3 deliberately removed. Re-running gap1
-- would therefore have reverted BOTH the gap2 creator clause AND the gap3
-- bypass removal, silently reopening a fail-open RBAC hole on Bill /
-- BillCredit / Expense / Project by-id reads. That is what this stub prevents.
--
-- NOTE: the prose below is the ORIGINAL header, preserved verbatim for
-- lineage. It describes the fail-open behavior AS IT WAS IN 2026-05-06 — not
-- how the canonical UDFs behave now. For current behavior read
-- shared/sql/dbo.access_udfs.sql.
-- ---------------------------------------------------------------------------

-- Gap 1 — UserProject access-check helper functions.
--
-- Four scalar UDFs that return BIT (1=accessible, 0=denied). All four
-- return 1 when @ActorIsSystemAdmin = 1 OR @ActorUserId IS NULL —
-- matching the Phase 3 NULL-bypass back-compat pattern.
--
-- Used by the per-entity read sprocs in gap1 follow-up migrations to
-- compress the EXISTS clauses into a single function call.
--
-- WITH SCHEMABINDING on the read-only ones lets SQL Server's Intelligent
-- Query Processing (2019+) inline them, so the perf hit vs inline
-- EXISTS is minimal.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO
