-- Migration 006 — switch Graph identifier columns to case-sensitive collation.
--
-- Defensive hardening following the Phase 6 phantom-EmailAttachment audit
-- (2026-05-27). Microsoft Graph identifiers (GraphMessageId,
-- InternetMessageId, GraphAttachmentId) are opaque IDs whose case is
-- significant — base64-encoded message stems can differ at a single
-- character position, and treating them as case-insensitive lets two
-- distinct identifiers collide.
--
-- The 252 legacy phantom EmailAttachments in prod were caused by the
-- pre-Phase-3 UpsertEmailMessage sproc matching case-different
-- GraphMessageId values as equal, which Phase 3's migration 004 fixed
-- by switching the merge key to (InternetMessageId, Folder). This
-- migration locks down the same risk at the column-storage level so no
-- future query path can re-introduce the bug.
--
-- Steps:
--   1. Drop indexes that include the three columns (their key metadata
--      is bound to the column collation).
--   2. ALTER COLUMN each ID column to Latin1_General_BIN (binary,
--      case-sensitive, accent-sensitive).
--   3. Recreate the indexes.
--
-- Safe to run repeatedly: each step guards with EXISTS / DROP IF EXISTS.

GO

-- Step 1: drop indexes that include the three columns.

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_EmailAttachment_Email_Graph'
      AND object_id = OBJECT_ID('dbo.EmailAttachment')
)
BEGIN
    DROP INDEX UQ_EmailAttachment_Email_Graph ON dbo.[EmailAttachment];
END
GO

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailMessage_GraphMessageId'
      AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    DROP INDEX IX_EmailMessage_GraphMessageId ON dbo.[EmailMessage];
END
GO

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_EmailMessage_InternetMessageId_Folder'
      AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    DROP INDEX UQ_EmailMessage_InternetMessageId_Folder ON dbo.[EmailMessage];
END
GO


-- Step 2: switch column collation to Latin1_General_BIN.
-- Length / nullability preserved.

ALTER TABLE dbo.[EmailMessage]
    ALTER COLUMN [GraphMessageId] NVARCHAR(255)
        COLLATE Latin1_General_BIN NOT NULL;
GO

ALTER TABLE dbo.[EmailMessage]
    ALTER COLUMN [InternetMessageId] NVARCHAR(255)
        COLLATE Latin1_General_BIN NULL;
GO

ALTER TABLE dbo.[EmailAttachment]
    ALTER COLUMN [GraphAttachmentId] NVARCHAR(255)
        COLLATE Latin1_General_BIN NOT NULL;
GO


-- Step 3: recreate the three indexes. They now compare under the new
-- binary collation by virtue of the column-level setting.

CREATE INDEX IX_EmailMessage_GraphMessageId
    ON dbo.[EmailMessage] ([GraphMessageId]);
GO

CREATE UNIQUE NONCLUSTERED INDEX UQ_EmailMessage_InternetMessageId_Folder
    ON dbo.[EmailMessage] ([InternetMessageId], [Folder])
    WHERE [InternetMessageId] IS NOT NULL;
GO

CREATE UNIQUE INDEX UQ_EmailAttachment_Email_Graph
    ON dbo.[EmailAttachment] ([EmailMessageId], [GraphAttachmentId]);
GO
