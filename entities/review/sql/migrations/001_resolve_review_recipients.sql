-- Phase 1c — Review Notifications  (historical migration; body superseded — see U-062 banner below)
-- Resolves the user list to notify when a Review is submitted on a Bill.
-- Walk: Bill -> BillLineItem -> Project -> UserProject (filtered to
-- 'Project Manager' / 'Owner' roles) -> User -> Contact (first non-null
-- Email per user, picked by Contact.Id ascending).
--
-- Bills span multiple projects via their line items; this resolver
-- unions across all distinct ProjectIds on the bill. Recipients are
-- deduped by UserId with PM beating Owner when a user holds both roles
-- across the bill's projects.
--
-- Recipients without an email row are still returned (Email = NULL)
-- so the caller can log + count them. The caller filters at send time.

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-062) — sproc body removed, NOT the intent.
-- Canonical definition now lives in exactly ONE place:
--   entities/review/sql/dbo.review.sql
-- Sproc formerly redefined here: dbo.ResolveReviewRecipientsByBillId
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is the single-source hazard.
-- ---------------------------------------------------------------------------


PRINT 'SUPERSEDED (U-062): no sprocs applied; canonical definitions live in entities/review/sql/dbo.review.sql.';
