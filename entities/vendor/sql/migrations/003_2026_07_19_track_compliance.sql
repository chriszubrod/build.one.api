-- Migration 003 (U-089): Vendor.TrackCompliance roster flag.
-- =============================================================================
-- Adds Vendor.TrackCompliance (compliance-roster flag, default 0).
-- Re-issues Read/Create/Update sprocs with the new column.
-- Idempotent. Safe to re-run. Do NOT edit dbo.vendor.sql — apply via this file.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

IF COL_LENGTH('dbo.Vendor', 'TrackCompliance') IS NULL
    ALTER TABLE dbo.[Vendor] ADD [TrackCompliance] BIT NOT NULL CONSTRAINT DF_Vendor_TrackCompliance DEFAULT (0);
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-142, 2026-07-24) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Re-issue Read/Create/Update sprocs to include TrackCompliance.
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/vendor/sql/dbo.vendor.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.CreateVendor
--   dbo.ReadVendors
--   dbo.ReadVendorById
--   dbo.ReadVendorByPublicId
--   dbo.ReadVendorByName
--   dbo.UpdateVendorById
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------

PRINT 'Vendor TrackCompliance migration applied: column added (sproc re-issue superseded by U-142).';
