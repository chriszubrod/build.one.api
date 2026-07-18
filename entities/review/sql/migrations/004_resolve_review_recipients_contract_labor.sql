-- =============================================================================
-- 2026-05-28 — Recipient resolver for ContractLabor reviews. (historical migration; body superseded — see U-062 banner below)
--
-- Walks: ContractLabor → ContractLaborLineItem (distinct ProjectId)
--                     → UserProject (filtered to 'Project Manager' / 'Owner')
--                     → User → Contact (first non-null Email per user)
--
-- A ContractLabor row spans the projects its line items reference (overhead
-- lines have NULL ProjectId; they're excluded). Dedupe by UserId with PM
-- precedence when a user holds both roles across the labor's projects.
--
-- Same envelope as ResolveReviewRecipientsByBillId so callers can share
-- post-processing code.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-062) — sproc body removed, NOT the intent.
-- Canonical definition now lives in exactly ONE place:
--   entities/review/sql/dbo.review.sql
-- Sproc formerly redefined here: dbo.ResolveReviewRecipientsByContractLaborId
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is the single-source hazard.
-- ---------------------------------------------------------------------------


PRINT 'SUPERSEDED (U-062): no sprocs applied; canonical definitions live in entities/review/sql/dbo.review.sql.';
