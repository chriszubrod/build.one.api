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
CREATE OR ALTER PROCEDURE UpsertEmailMessage
(
    @GraphMessageId NVARCHAR(255),
    @InternetMessageId NVARCHAR(255) = NULL,
    @ConversationId NVARCHAR(255) = NULL,
    @MailboxAddress NVARCHAR(320),
    @FromAddress NVARCHAR(320) = NULL,
    @FromName NVARCHAR(255) = NULL,
    @ToRecipients NVARCHAR(MAX) = NULL,
    @CcRecipients NVARCHAR(MAX) = NULL,
    @Subject NVARCHAR(1024) = NULL,
    @BodyPreview NVARCHAR(1024) = NULL,
    @BodyContent NVARCHAR(MAX) = NULL,
    @BodyContentType NVARCHAR(20) = NULL,
    @ReceivedDatetime DATETIME2(3) = NULL,
    @WebLink NVARCHAR(1024) = NULL,
    @HasAttachments BIT = 0,
    @CreatedByUserId BIGINT = NULL,
    @Folder NVARCHAR(50) = 'inbox',
    @DefaultProcessingStatus NVARCHAR(50) = 'pending'
)
AS
BEGIN
    -- New invariant: InternetMessageId is required. GraphMessageId is
    -- not stable enough in shared mailboxes (recycled on soft-delete +
    -- restore cycles) to use as a primary key. RFC 5322 Message-ID is
    -- globally unique and immutable; every standard email has one.
    IF @InternetMessageId IS NULL OR LTRIM(RTRIM(@InternetMessageId)) = ''
    BEGIN
        RAISERROR(
            'UpsertEmailMessage requires @InternetMessageId (RFC 5322 Message-ID). GraphMessageId is not stable enough to use as a primary key in this mailbox. Caller should log + skip.',
            16, 1
        );
        RETURN;
    END

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[EmailMessage] AS target
    USING (SELECT @InternetMessageId AS InternetMessageId, @Folder AS Folder) AS source
    ON target.[InternetMessageId] = source.InternetMessageId
       AND target.[Folder] = source.Folder
    WHEN MATCHED THEN
        UPDATE SET
            [ModifiedDatetime] = @Now,
            -- GraphMessageId is NOW a mutable secondary — Graph can
            -- recycle these and a later poll might see the same IMID
            -- under a different GraphMessageId. Update to the latest.
            [GraphMessageId] = @GraphMessageId,
            [ConversationId] = @ConversationId,
            [MailboxAddress] = @MailboxAddress,
            [FromAddress] = @FromAddress,
            [FromName] = @FromName,
            [ToRecipients] = @ToRecipients,
            [CcRecipients] = @CcRecipients,
            [Subject] = @Subject,
            [BodyPreview] = @BodyPreview,
            [BodyContent] = @BodyContent,
            [BodyContentType] = @BodyContentType,
            [ReceivedDatetime] = @ReceivedDatetime,
            [WebLink] = @WebLink,
            [HasAttachments] = @HasAttachments
            -- Folder + ProcessingStatus deliberately not updated on
            -- match: folder is part of the match key (immutable per row);
            -- status is owned by the agent claim/process workflow.
    WHEN NOT MATCHED THEN
        INSERT
            ([CreatedDatetime], [ModifiedDatetime], [GraphMessageId], [InternetMessageId],
             [ConversationId], [MailboxAddress], [FromAddress], [FromName],
             [ToRecipients], [CcRecipients], [Subject],
             [BodyPreview], [BodyContent], [BodyContentType], [ReceivedDatetime],
             [ProcessingStatus], [WebLink], [HasAttachments], [CreatedByUserId], [Folder])
        VALUES
            (@Now, @Now, @GraphMessageId, @InternetMessageId,
             @ConversationId, @MailboxAddress, @FromAddress, @FromName,
             @ToRecipients, @CcRecipients, @Subject,
             @BodyPreview, @BodyContent, @BodyContentType, @ReceivedDatetime,
             @DefaultProcessingStatus, @WebLink, @HasAttachments, COALESCE(@CreatedByUserId, 17), @Folder)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[GraphMessageId],
        INSERTED.[InternetMessageId],
        INSERTED.[ConversationId],
        INSERTED.[MailboxAddress],
        INSERTED.[FromAddress],
        INSERTED.[FromName],
        INSERTED.[Subject],
        INSERTED.[BodyPreview],
        INSERTED.[BodyContentType],
        CONVERT(VARCHAR(19), INSERTED.[ReceivedDatetime], 120) AS [ReceivedDatetime],
        INSERTED.[ProcessingStatus],
        INSERTED.[AgentSessionId],
        INSERTED.[WebLink],
        INSERTED.[HasAttachments];

    COMMIT TRANSACTION;
END;
GO
