-- =============================================================================
-- 2026-05-27 — Phase 2: rate storage in DB.
--
-- Adds Vendor.HourlyRate + Vendor.Markup columns (default rates per vendor).
-- Re-issues CreateVendor + UpdateVendorById with the new params.
-- Per-project overrides live in dbo.VendorProjectRate (separate migration).
--
-- Default-rate lookup precedence at aggregation time (Phase 4):
--   1. dbo.VendorProjectRate (VendorId, ProjectId) override row → rate, markup
--   2. dbo.Vendor.HourlyRate, Vendor.Markup (this column)
--   3. ERROR — aggregation refuses to write $0 silently
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- Column additions ------------------------------------------------------------
IF COL_LENGTH('dbo.[Vendor]', 'HourlyRate') IS NULL
    ALTER TABLE [dbo].[Vendor] ADD [HourlyRate] DECIMAL(18,4) NULL;
GO

IF COL_LENGTH('dbo.[Vendor]', 'Markup') IS NULL
    ALTER TABLE [dbo].[Vendor] ADD [Markup] DECIMAL(18,4) NULL;
GO


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-142, 2026-07-24) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Re-issue Read/Create/Update sprocs to include HourlyRate and Markup.
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

PRINT 'Vendor rate-column migration applied: HourlyRate/Markup columns added (sproc re-issue superseded by U-142).';
