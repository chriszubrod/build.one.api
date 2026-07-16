-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-050, 2026-07-16) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Gap 1 — Project list scoped by UserProject membership.
--
-- This file formerly carried the fail-open `OR @ActorUserId IS NULL` bypass
-- (4 occurrences). Re-running it would reintroduce a cross-user data leak.
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

-- Gap 1 — Project list scoped by UserProject membership.
-- Per Q1.2 = (a): non-admin users see only Projects they have a
-- UserProject row for. System admins (@ActorIsSystemAdmin = 1) bypass.
-- NULL @ActorUserId also bypasses (back-compat during deploy).
--
-- Idempotent stub (U-050): sproc bodies removed; re-running applies nothing.

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO
