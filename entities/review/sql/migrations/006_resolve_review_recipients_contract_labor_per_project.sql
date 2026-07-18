-- =============================================================================
-- 2026-06-03 — Per-project recipient resolver for ContractLabor reviews. (v1; historical migration, body superseded — see U-062 banner below)
--
-- Distinct from ResolveReviewRecipientsByContractLaborId (which dedupes by
-- UserId for the bill-style single-email pattern). This one returns ONE
-- ROW PER (Project, PM) so the notification service can build a separate
-- draft per project on the ContractLabor.
--
-- Projects with NO PM still surface (left-join shape) so the caller can
-- still create a draft with an empty TO list for projects lacking a
-- configured Project Manager — per Chris' product decision: never auto-
-- send, just create the draft for manual address-and-send.
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
