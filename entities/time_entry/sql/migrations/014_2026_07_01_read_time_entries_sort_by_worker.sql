-- =============================================================================
-- 2026-07-01 — allow @SortBy = 'Worker' in ReadTimeEntries (ORIGINAL INTENT).
--
-- ORIGINAL PROBLEM: This migration MISTARGETED the dead `ReadTimeEntries` singular
-- sproc with a 10-param PAGINATED, UNSCOPED body (page/search/sort params) when
-- the list-page Worker sort actually belongs in `ReadTimeEntriesPaginated`.
--
-- `ReadTimeEntries`'s only caller is TimeEntryService.read_all() — a dead path with
-- no HTTP or agent caller — and it takes the scoped 3-param actor signature
-- (@ActorUserId, @ActorIsSystemAdmin, @ActorCanViewTeam).
--
-- Reconciled (U-039, 2026-07-16) to the canonical scoped 3-param `ReadTimeEntries`
-- from entities/time_entry/sql/dbo.time_entry.sql so a re-run is a safe no-op.
-- Previously, re-running this file reverted RBAC scoping and re-drifted prod.
--
-- The list-page Worker sort belongs in `ReadTimeEntriesPaginated` (the scoped
-- paginated sproc the /time-entries endpoint actually calls) and is DEFERRED to
-- a separate follow-up unit — NOT added here.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-045, 2026-07-16) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Mistargeted Worker sort on ReadTimeEntries; reconciled to scoped 3-param body.
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/time_entry/sql/dbo.time_entry.sql
--
-- Sprocs formerly redefined here (now canonical in the base file):
--   dbo.ReadTimeEntries
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------

PRINT 'SUPERSEDED (U-045): no sprocs applied; canonical definitions live in entities/time_entry/sql/dbo.time_entry.sql.';
