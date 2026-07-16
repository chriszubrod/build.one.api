-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-050, 2026-07-16) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Gap 1 follow-up — remove the @ActorUserId IS NULL legacy-caller bypass
--   from Project list/read sprocs (fail-closed).
--
-- Its sproc bodies OMITTED the [Notes] column — a regression vs the base file
-- introduced 2026-05-12 (Project.Notes was added 2026-05-07) — so re-running
-- it would silently drop Notes from the read surface.
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/project/sql/dbo.project.sql
--
-- Sprocs formerly redefined here (now canonical in the base file):
--   ReadProjects, ReadProjectById, ReadProjectByPublicId, ReadProjectByName
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-05-12 bypass leak path and the Notes-column regression.
-- ---------------------------------------------------------------------------

-- Gap 1 follow-up (2026-05-12) — remove the `@ActorUserId IS NULL`
-- legacy-caller bypass from Project list/read sprocs. Replaces
-- 001_gap1_scope_by_user_project.sql.
--
-- Background: 001 included `OR @ActorUserId IS NULL` so that pre-Gap-1
-- callers (services that hadn't yet learned to thread actor context)
-- would keep working during the staged deploy. After a leak was
-- discovered on the TimeEntry side where this bypass turned into a
-- silent "no actor → show everything" path, we removed it here too
-- to fail closed across the entire user-scoped read surface.
--
-- Scheduler / system callers (X-Drain-Secret-gated admin endpoints
-- in `shared/api/admin.py::_require_drain_secret`) now explicitly
-- set `current_is_system_admin = True` so the sproc's
-- @ActorIsSystemAdmin = 1 clause grants them all-user visibility
-- via the intended path, not via a silent bypass.
--
-- Idempotent stub (U-050): sproc bodies removed; re-running applies nothing.

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO
