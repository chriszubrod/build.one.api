-- Gap 3 — Credential lifecycle.
-- Adds RevokeAllAuthRefreshTokensByAuthId sproc — bulk-revokes every
-- non-revoked refresh token for a given auth_id. Used by both:
--   - User self-service password change (AuthService.change_password)
--   - Admin password reset (AuthService.set_credentials_for_user)
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-126, 2026-07-23) — sproc body removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Bulk-revoke all refresh tokens for an Auth row on password change/reset.
--
-- The canonical definition of this sproc now lives in exactly ONE place:
--   entities/auth/sql/dbo.auth.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.RevokeAllAuthRefreshTokensByAuthId
--
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------
