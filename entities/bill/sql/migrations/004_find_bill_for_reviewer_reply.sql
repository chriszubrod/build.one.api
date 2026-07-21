-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc body removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Migration 004 — FindBillForReviewerReply sproc (Wave 3 Phase 1 fuzzy
--   fallback for reviewer-reply matching).
--
--   Context: 2026-05-19 manual walk surfaced that the reviewer-reply
--   primitive (ReadBillByConversationId) is too strict — when a PM replies
--   from a non-Outlook client, the ConversationId may not survive the round
--   trip, and parsed approvals fall on the floor as `internal_reply` +
--   `flagged_needs_review`. The Bill that the PM was reviewing exists in
--   our DB; we just can't link the reply back. TODO.md line 203 specifies
--   a two-phase fix; this is Phase 1 only.
--
--   New sproc: same conv_id match path as the existing sproc, plus a
--   fuzzy fallback on (BillNumber exact match) AND (any line item on a
--   Project whose Name contains the hint substring). Returns nothing when
--   0 or 2+ candidates match — preserves the existing
--   `flagged_needs_review` outcome for ambiguous cases.
--
--   Idempotent: CREATE OR ALTER. Safe to re-apply.
--
-- The canonical definition now lives in exactly ONE place:
--   entities/bill/sql/dbo.bill_create_source_email.sql
--
-- Sproc formerly redefined here: dbo.FindBillForReviewerReply
--
-- Re-running this file is now a no-op. Do NOT reintroduce a body here.
--
-- DANGER (motivated U-100): re-applying would redefine FindBillForReviewerReply
-- outside its canonical base file, reintroducing single-source drift on the
-- reviewer-reply fuzzy-match path.
-- ---------------------------------------------------------------------------

GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definition lives in entities/bill/sql/dbo.bill_create_source_email.sql.';
