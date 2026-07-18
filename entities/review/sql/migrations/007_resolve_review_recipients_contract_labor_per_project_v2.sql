-- =============================================================================
-- 2026-06-03 — v2 of the per-project CL recipient resolver: include Owners. (historical migration; body superseded — see U-062 banner below)
--
-- Mirrors Bill's ResolveReviewRecipientsByBillId envelope: PMs go TO,
-- Owners go CC. Caller splits on RoleName. RolePrecedence column is
-- included so the caller can rank consistently if a user holds both
-- roles on the same project (PM wins).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-062) — sproc body removed, NOT the intent.
-- Canonical definition now lives in exactly ONE place:
--   entities/review/sql/dbo.review.sql
-- Sproc formerly redefined here: dbo.ResolveContractLaborReviewRecipientsPerProject
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is the single-source hazard.
-- ---------------------------------------------------------------------------


PRINT 'SUPERSEDED (U-062): no sprocs applied; canonical definitions live in entities/review/sql/dbo.review.sql.';
