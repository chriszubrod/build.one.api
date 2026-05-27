-- Migration 005 — drop UQ_EmailMessage_GraphMessageId; replace with a
-- non-unique lookup index.
--
-- Phase 3 follow-on. Migration 004 switched the upsert MERGE key to
-- (InternetMessageId, Folder) but left the table's existing UNIQUE index
-- on GraphMessageId in place. After Phase 2 repair, our DB legitimately
-- has rows with GraphMessageIds that Graph has since RECYCLED to point
-- at new emails (the very recycling behavior we're hardening against).
-- New emails arriving from Graph fail INSERT with constraint violation
-- on UQ_EmailMessage_GraphMessageId.
--
-- The truth: GraphMessageId is now treated as a mutable secondary
-- identifier. It cannot have a uniqueness invariant. A non-unique index
-- is still useful for `ReadEmailMessageByGraphMessageId` lookups (used
-- by the polling pipeline + reviewer-reply ConversationId resolver).
--
-- Idempotent: IF EXISTS guards on both drop + create.
GO


-- The original was a UNIQUE CONSTRAINT (ALTER TABLE ADD CONSTRAINT),
-- not a standalone index, so it needs ALTER TABLE DROP CONSTRAINT.
IF EXISTS (
    SELECT 1 FROM sys.key_constraints
    WHERE name = 'UQ_EmailMessage_GraphMessageId'
      AND parent_object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage]
        DROP CONSTRAINT UQ_EmailMessage_GraphMessageId;
    PRINT '  Dropped UQ_EmailMessage_GraphMessageId constraint';
END
ELSE IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_EmailMessage_GraphMessageId'
      AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    -- Fallback path if it was created as a unique index rather than a constraint
    DROP INDEX UQ_EmailMessage_GraphMessageId ON dbo.[EmailMessage];
    PRINT '  Dropped UQ_EmailMessage_GraphMessageId index';
END
ELSE
    PRINT '  UQ_EmailMessage_GraphMessageId already absent';
GO


IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailMessage_GraphMessageId'
      AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_EmailMessage_GraphMessageId
        ON dbo.[EmailMessage] ([GraphMessageId]);
    PRINT '  Created IX_EmailMessage_GraphMessageId (non-unique lookup)';
END
ELSE
    PRINT '  IX_EmailMessage_GraphMessageId already present';
GO
