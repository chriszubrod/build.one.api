-- U-300c: Phase-6 guarded DROP of qbo.Attachable / qbo.AttachableAttachment.
--
-- STAGED, NOT APPLIED. Per feedback_builders_never_mutate_prod_data, the build
-- unit prepares this file; /em runs it after (a) U-300c-prereq (f37b0410) has
-- soaked >=24h / >=6 QBO pull-push cycles per docs/design/u300c.md §6, and
-- (b) a fresh re-run of §5's live re-verify query returns 0 for every check —
-- do NOT trust this file's or the design doc's cached row counts, they decay
-- the moment they're written.
--
-- Drop order: qbo.AttachableAttachment (child, clears both its outward FKs)
-- before qbo.Attachable (parent) -- FK_AttachableAttachment_QboAttachable and
-- FK_AttachableAttachment_Attachment are the only 2 edges touching either
-- table (live sys.foreign_keys query, docs/design/u300c.md §1); no other
-- table FKs into either, so this is a same-family, 2-table drop.
--
-- Re-verify immediately before running (docs/design/u300c.md §5):
--   1. Zero rows created/modified in either table since U-300c-prereq's own
--      deploy timestamp (substitute that timestamp for the query below).
--   2. Row-count context (not a gate, just confirms nothing unexpected grew).
--   3. Zero qbo.ReconciliationIssue rows referencing either table since that
--      same deploy timestamp.
-- Plus a fresh re-grep of sync_attachment_to_qbo's callers, identity_drift.py,
-- and reconciliation/outbox files against whatever HEAD is about to ship this.

IF OBJECT_ID('qbo.AttachableAttachment', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[AttachableAttachment];
END;
GO

IF OBJECT_ID('qbo.Attachable', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[Attachable];
END;
GO

-- Sprocs orphaned by the table drop. SQL Server does not require dropping
-- these first -- an unbound sproc body only errors at EXECUTE, not at DROP
-- TABLE time -- but leaving them live is a footgun: a stray caller gets a
-- confusing runtime error instead of an import-time failure.
-- (4 siblings -- ReadQboAttachablesByEntityRef, ReadQboAttachablesByRealmId,
-- DeleteQboAttachableByQboId, ReadAttachableAttachmentById -- were already
-- retired by U-286 2026-08-20 and are not repeated here.)
DROP PROCEDURE IF EXISTS dbo.CreateQboAttachable;
DROP PROCEDURE IF EXISTS dbo.ReadQboAttachableById;
DROP PROCEDURE IF EXISTS dbo.ReadQboAttachableByQboId;
DROP PROCEDURE IF EXISTS dbo.ReadQboAttachableByQboIdAndRealmId;
DROP PROCEDURE IF EXISTS dbo.UpdateQboAttachableByQboId;
DROP PROCEDURE IF EXISTS dbo.CreateAttachableAttachment;
DROP PROCEDURE IF EXISTS dbo.ReadAttachableAttachmentByAttachmentId;
DROP PROCEDURE IF EXISTS dbo.ReadAttachableAttachmentByQboAttachableId;
DROP PROCEDURE IF EXISTS dbo.DeleteAttachableAttachmentById;
GO
