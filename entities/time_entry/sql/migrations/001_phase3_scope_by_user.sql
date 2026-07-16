-- =====================================================================
-- SUPERSEDED (U-045, 2026-07-16) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Phase 3 Access Control Rebuild — row scoping TimeEntry/TimeLog/TimeEntryStatus
--   by UserId (ORIGINAL 2026 migration; current behavior is 3-param fail-closed
--   in the base file).
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/time_entry/sql/dbo.time_entry.sql
--
-- Sprocs formerly redefined here (now canonical in the base file):
--   the 16 RBAC-scoped TimeEntry/TimeLog/TimeEntryStatus read+mutation sprocs —
--   see entities/time_entry/sql/dbo.time_entry.sql for the canonical set.
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- =====================================================================

-- Phase 3 — Access Control Rebuild — TimeEntry / TimeLog / TimeEntryStatus
-- row scoping by UserId.
--
-- Idempotent stub (U-045): sproc bodies removed; re-running applies nothing.
--
-- Scope rule (ORIGINAL 2026 migration intent — NOT current behavior):
--   System admins (@ActorIsSystemAdmin = 1) bypass.
--   Legacy callers (@ActorUserId IS NULL) bypass — preserves
--     pre-Phase 3 behavior during the staged deploy. Service code
--     deploy is what activates filtering.
--   Otherwise the row's parent TimeEntry.UserId must match
--     @ActorUserId. For TimeLog and TimeEntryStatus this is enforced
--     by joining through TimeEntry.
--
-- Applies to every read/update/delete on TimeEntry and its children.
-- Create paths are unaffected — callers stamp UserId on insert and
-- the service layer prevents impersonation at the API surface.

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO

PRINT 'SUPERSEDED (U-045): no sprocs applied; canonical definitions live in entities/time_entry/sql/dbo.time_entry.sql.';
