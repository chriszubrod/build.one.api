-- Migration 004 — switch UpsertEmailMessage MERGE key from GraphMessageId
-- to (InternetMessageId, Folder).
--
-- Phase 3 of the 2026-05-27 data-corruption investigation. Root cause
-- (Phase 1) was MS Graph recycling GraphMessageId values in the
-- invoice@rogersbuild.com shared mailbox when emails are deleted-then-
-- restored, causing UpsertEmailMessage's GraphMessageId-keyed MERGE to
-- overwrite existing rows with metadata from unrelated later emails.
-- Phase 2 repair (scripts/repair_email_message_graph_id_drift.py)
-- restored 160 corrupted rows. Phase 3 prevents recurrence at the sproc
-- level.
--
-- New invariant: InternetMessageId (RFC 5322 Message-ID header) is the
-- stable identity. Required for every poll. (Folder) distinguishes the
-- same forwarded email in inbox vs. sentitems.
--
-- GraphMessageId becomes a mutable secondary field — updated on every
-- successful match so callers querying Graph by it stay current.
--
-- Defensive: hard-fail (RAISERROR) when @InternetMessageId IS NULL.
-- 0 EmailMessages in prod have NULL InternetMessageId today (verified
-- 2026-05-27). Every standard SMTP server stamps one. If ever
-- encountered, the caller (_ingest_messages) logs to errors and skips
-- that message — better than silently inserting under the broken key.
--
-- Rollback plan: drop the unique index + restore the old sproc body
-- (saved below in a commented-out block).
--
-- Idempotent: CREATE OR ALTER sproc + IF NOT EXISTS guard on the
-- unique index.
GO


-- Step 1: Filtered unique index on (InternetMessageId, Folder).
-- Only enforces uniqueness on non-null IMIDs (defensive — works with
-- legacy rows even if some unexpectedly have NULL IMID, which today's
-- audit says is zero rows but the filter survives the edge case).
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_EmailMessage_InternetMessageId_Folder'
      AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX UQ_EmailMessage_InternetMessageId_Folder
        ON dbo.[EmailMessage] ([InternetMessageId], [Folder])
        WHERE [InternetMessageId] IS NOT NULL;
END
GO


-- Step 2: UpsertEmailMessage with new MERGE on (InternetMessageId, Folder).
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-789, 2026-11-12) — body removed, NOT the (InternetMessageId, Folder) merge-key intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/email_message/sql/dbo.email_message.sql
--
-- This copy was byte-identical to prod (verified 2026-11-12), so nothing is lost: the
-- base file carries the same body. Migrations carry pointer stubs only, so that the
-- single-source ratchet can see one definition instead of three.
-- Re-running this file is now a no-op for UpsertEmailMessage. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO
