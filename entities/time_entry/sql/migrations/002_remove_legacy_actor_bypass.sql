-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-045, 2026-07-16) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Phase 3 follow-up — remove the @ActorUserId IS NULL legacy-caller bypass
--   from TimeEntry/TimeLog/TimeEntryStatus read sprocs (fail-closed).
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
-- ---------------------------------------------------------------------------

-- Phase 3 follow-up (2026-05-12) — remove the `@ActorUserId IS NULL`
-- legacy-caller bypass from TimeEntry / TimeLog / TimeEntryStatus
-- read sprocs. Replaces 001_phase3_scope_by_user.sql.
--
-- Background: 001 included `OR @ActorUserId IS NULL` in every WHERE
-- clause so that pre-Phase-3 callers (service code that hadn't yet
-- learned to thread actor context) would keep working during the
-- staged deploy. Service code is fully rolled out now, so the clause
-- has become a silent leak path — any caller that fails to populate
-- the `current_user_id` ContextVar (e.g., a regressed auth middleware,
-- a scheduler endpoint that forgot to set system context) silently
-- returns every row instead of failing closed.
--
-- This migration removes the bypass everywhere. New scope rule:
--   System admins (@ActorIsSystemAdmin = 1) bypass.
--   Otherwise the row's parent TimeEntry.UserId must match
--     @ActorUserId. NULL @ActorUserId → no rows (fail closed).
--
-- Idempotent stub (U-045): sproc bodies removed; re-running applies nothing.
--
-- Affected paths: every read on TimeEntry and its children. Create
-- paths unaffected. Scheduler / system callers that legitimately need
-- to see all rows must set `current_is_system_admin = True` in the
-- ContextVar before invoking the service — see commit notes.

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO
