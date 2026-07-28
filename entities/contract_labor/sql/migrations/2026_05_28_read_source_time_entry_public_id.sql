-- =============================================================================
-- 2026-05-28 — surface SourceTimeEntryPublicId on the detail read.
--
-- The React ContractLabor Edit page wants to embed the source TimeEntry's
-- per-log breakdown ("Time Log Details" section). It already has the
-- TimeEntry detail endpoint (`GET /api/v1/time-entries/{public_id}`); it
-- just needs the source PublicId to address it. SourceTimeEntryId (BIGINT)
-- is opaque to the client.
--
-- LEFT JOIN to dbo.TimeEntry so Excel-imported rows (NULL SourceTimeEntryId)
-- return NULL for the new column.
--
-- Idempotent — CREATE OR ALTER. (SUPERSEDED by U-162: this file no longer
-- defines the sproc; see the stub below.)
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-162, 2026-07-28) — sproc body removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Surface SourceTimeEntryPublicId on the detail read via LEFT JOIN to TimeEntry.
--
-- The canonical definition of this sproc now lives in exactly ONE place:
--   entities/contract_labor/sql/dbo.contract_labor.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.ReadContractLaborByPublicId
--
-- Drift: NONE — all three bodies were byte-identical; stubbed for single-source
-- only. (The base additionally carried a second, byte-identical copy of this
-- sproc; U-162 collapsed it so the base defines each sproc exactly once.)
--
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------
GO

PRINT 'SUPERSEDED (U-162): ReadContractLaborByPublicId is canonical in entities/contract_labor/sql/dbo.contract_labor.sql; no sproc applied here.';
