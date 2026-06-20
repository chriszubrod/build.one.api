-- EmailMessage entity — one row per polled email message in the
-- shared invoice inbox. Owns its child EmailAttachment rows. The
-- ProcessingStatus column drives the email-agent state machine:
--
--   pending          newly polled, not yet processed
--   processing       agent run in flight
--   extracted        DI ran successfully on all attachments
--   awaiting_review  agent classified or extraction had issues; human-eyes
--   agent_complete   agent finished, downstream specialist took it
--   irrelevant       agent classified as non-invoice, no action taken
--   failed           hard error during polling/extraction/agent
--
-- Idempotency:
--   GraphMessageId is unique. The poll service does an upsert on this
--   key so re-polling never creates duplicates.
--
-- AgentSessionId links to the AgentSession (intelligence) that
-- processed this email; NULL until the agent kicks off.

GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[EmailMessage]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    -- Graph identifiers use binary collation so case matters when
    -- comparing them. Default DB collation is case-insensitive and
    -- would treat case-twin base64 IDs as equal — see migration 006.
    [GraphMessageId] NVARCHAR(255) COLLATE Latin1_General_BIN NOT NULL,
    [InternetMessageId] NVARCHAR(255) COLLATE Latin1_General_BIN NULL,
    [ConversationId] NVARCHAR(255) NULL,
    [MailboxAddress] NVARCHAR(320) NOT NULL,
    [FromAddress] NVARCHAR(320) NULL,
    [FromName] NVARCHAR(255) NULL,
    [Subject] NVARCHAR(1024) NULL,
    [BodyPreview] NVARCHAR(1024) NULL,
    [BodyContent] NVARCHAR(MAX) NULL,
    [BodyContentType] NVARCHAR(20) NULL,
    [ReceivedDatetime] DATETIME2(3) NULL,
    [ProcessingStatus] NVARCHAR(50) NOT NULL DEFAULT 'pending',
    [LastError] NVARCHAR(MAX) NULL,
    [AgentSessionId] BIGINT NULL,
    [WebLink] NVARCHAR(1024) NULL,
    [HasAttachments] BIT NOT NULL DEFAULT 0,
    -- Agent classification stamp (set by mark_email_outcome). Captures
    -- the email_specialist's *semantic* decision (what kind of doc this
    -- was + what action it took) — independent of ProcessingStatus
    -- (which tracks workflow). Powers search_email_sender_history so
    -- prior emails inform the next classification.
    [AgentClassification] NVARCHAR(50) NULL,
    [AgentClassificationReason] NVARCHAR(1024) NULL,
    [AgentDecidedAction] NVARCHAR(50) NULL,
    [AgentClassificationConfidence] DECIMAL(5,4) NULL,
    [Folder] NVARCHAR(50) NOT NULL DEFAULT 'inbox'
    -- No UNIQUE constraint on GraphMessageId. MS Graph recycles these
    -- ids in shared mailboxes (delete-then-restore cycles), so
    -- GraphMessageId is treated as a mutable secondary identifier.
    -- The stable identity is (InternetMessageId, Folder); see the
    -- filtered unique index below. Migrations 004 + 005 made this
    -- change in prod (2026-05-27 data-corruption investigation).
);
END
GO

-- Idempotent column adds for existing environments
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'AgentClassification' AND Object_ID = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [AgentClassification] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'AgentClassificationReason' AND Object_ID = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [AgentClassificationReason] NVARCHAR(1024) NULL;
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'AgentDecidedAction' AND Object_ID = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [AgentDecidedAction] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'AgentClassificationConfidence' AND Object_ID = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [AgentClassificationConfidence] DECIMAL(5,4) NULL;
END
GO

-- Retry counter for the recovery cron (see RecoverStuckProcessingEmailMessages).
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'ProcessingResetCount' AND Object_ID = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage]
        ADD [ProcessingResetCount] INT NOT NULL
            CONSTRAINT [DF_EmailMessage_ProcessingResetCount] DEFAULT (0);
END
GO

-- Folder discriminator. 'inbox' for inbound mail (the original poll path),
-- 'sentitems' for outbound forwards / replies we send (audit trail). The
-- DEFAULT applies to existing rows on add — no manual backfill needed.
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'Folder' AND Object_ID = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage]
        ADD [Folder] NVARCHAR(50) NOT NULL
            CONSTRAINT [DF_EmailMessage_Folder] DEFAULT ('inbox');
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailMessage_FromAddress_Classification' AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    -- Powers search_email_sender_history's group-by-classification query.
    CREATE INDEX IX_EmailMessage_FromAddress_Classification
        ON dbo.[EmailMessage] ([FromAddress], [AgentClassification]);
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailMessage_ProcessingStatus' AND object_id = OBJECT_ID('dbo.EmailMessage'))
BEGIN
CREATE INDEX IX_EmailMessage_ProcessingStatus ON [dbo].[EmailMessage] ([ProcessingStatus]);
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailMessage_ReceivedDatetime' AND object_id = OBJECT_ID('dbo.EmailMessage'))
BEGIN
CREATE INDEX IX_EmailMessage_ReceivedDatetime ON [dbo].[EmailMessage] ([ReceivedDatetime] DESC);
END
GO

-- Watermark lookup support. ReadMaxReceivedDatetimeByMailbox computes
-- MAX(ReceivedDatetime) per (MailboxAddress, Folder); this index lets it
-- be a single seek per mailbox/folder.
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailMessage_MailboxFolder_ReceivedDatetime' AND object_id = OBJECT_ID('dbo.EmailMessage'))
BEGIN
CREATE INDEX IX_EmailMessage_MailboxFolder_ReceivedDatetime
    ON [dbo].[EmailMessage] ([MailboxAddress], [Folder], [ReceivedDatetime] DESC);
END
GO

-- (Migration 004, 2026-05-27) Filtered unique index on the new primary
-- identity (InternetMessageId, Folder). InternetMessageId is the
-- RFC 5322 Message-ID header — globally unique + immutable, unlike
-- GraphMessageId which Microsoft recycles in shared mailboxes on
-- soft-delete + restore cycles.
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_EmailMessage_InternetMessageId_Folder' AND object_id = OBJECT_ID('dbo.EmailMessage'))
BEGIN
CREATE UNIQUE NONCLUSTERED INDEX UQ_EmailMessage_InternetMessageId_Folder
    ON dbo.[EmailMessage] ([InternetMessageId], [Folder])
    WHERE [InternetMessageId] IS NOT NULL;
END
GO

-- (Migration 005, 2026-05-27) Non-unique lookup index on GraphMessageId.
-- The original UNIQUE CONSTRAINT was dropped because Graph recycles ids
-- in shared mailboxes; the index stays for read-by-graph-id performance.
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailMessage_GraphMessageId' AND object_id = OBJECT_ID('dbo.EmailMessage'))
BEGIN
CREATE NONCLUSTERED INDEX IX_EmailMessage_GraphMessageId
    ON dbo.[EmailMessage] ([GraphMessageId]);
END
GO

-- (Phase B, 2026-05-28) ConversationId lookup index. Backs
-- ReadEmailMessagesByConversationId (sibling-thread context for the
-- email agent) + the existing ReadActiveConversationIds GROUP BY.
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailMessage_ConversationId' AND object_id = OBJECT_ID('dbo.EmailMessage'))
BEGIN
CREATE NONCLUSTERED INDEX IX_EmailMessage_ConversationId
    ON dbo.[EmailMessage] ([ConversationId], [ReceivedDatetime]);
END
GO

GO

-- Active conversation IDs from recent EmailMessage rows. Used by the
-- poll service (Wave 3 Phase C) to expand the inbox filter beyond
-- "Blue category" to also include any conversation we are already
-- tracking — so PM replies on a forwarded review notification get
-- ingested automatically without a manual category tag.
--
-- Returns DISTINCT non-null ConversationId values, ordered by the most
-- recent CreatedDatetime per conversation. TOP-bounded to keep the
-- generated $filter URL within reasonable length limits (each Graph
-- ConversationId is ~150 chars).
CREATE OR ALTER PROCEDURE ReadActiveConversationIds
(
    @SinceUtc DATETIME2(3),
    @MaxRows INT = 50
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@MaxRows)
        [ConversationId]
    FROM (
        SELECT [ConversationId], MAX([CreatedDatetime]) AS LastSeen
        FROM dbo.[EmailMessage]
        WHERE [ConversationId] IS NOT NULL
          AND [CreatedDatetime] >= @SinceUtc
        GROUP BY [ConversationId]
    ) g
    ORDER BY g.LastSeen DESC;
END;
GO

-- Sibling-thread context lookup. Returns header-only EmailMessage rows
-- belonging to the same Graph conversation thread, ordered oldest
-- → newest so the agent reads the chronology in order. Powers
-- read_email_thread (Phase B, 2026-05-28): the prior emails in the
-- same conversation are usually the strongest signal for what THIS
-- email means (e.g. NSW's collections email only makes sense alongside
-- the 4 prior exchanges).
CREATE OR ALTER PROCEDURE ReadEmailMessagesByConversationId
(
    @ConversationId NVARCHAR(255),
    @ExcludePublicId UNIQUEIDENTIFIER = NULL,
    @MaxRows INT = 50
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@MaxRows)
        [Id],
        CAST([PublicId] AS NVARCHAR(36))                  AS PublicId,
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)      AS CreatedDatetime,
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120)     AS ReceivedDatetime,
        [GraphMessageId],
        [InternetMessageId],
        [ConversationId],
        [MailboxAddress],
        [FromAddress],
        [FromName],
        [Subject],
        [BodyPreview],
        [HasAttachments],
        [Folder],
        [ProcessingStatus],
        [AgentClassification],
        [AgentClassificationReason],
        [AgentDecidedAction],
        [AgentClassificationConfidence]
    FROM dbo.[EmailMessage]
    WHERE [ConversationId] = @ConversationId
      AND (@ExcludePublicId IS NULL OR [PublicId] <> @ExcludePublicId)
    ORDER BY [ReceivedDatetime] ASC, [CreatedDatetime] ASC;
END;
GO

-- Reconcile Review.EmailMessageId on "In Review" rows that don't yet
-- carry an explicit forward link. Two scenarios produce these:
--
--   (a) Forward-going: notification_service writes the Review at
--       "In Review" the moment the MS outbox row is enqueued; the
--       forward only becomes an EmailMessage row when the next Sent
--       poll ingests it (~5 min later).
--   (b) Backfill: the forward was sent days/weeks ago via the prior
--       (unlinked) flow; the auto-advance to "In Review" only fires
--       now. Forward.ReceivedDatetime predates Review.CreatedDatetime
--       by a wide margin — the inverse of (a).
--
-- Matching strategy: pick the LATEST outbound forward on the Bill's
-- source ConversationId. Single-cycle Bills (the common case) have
-- exactly one forward, so this is unambiguous. Multi-cycle Bills
-- (declined+resubmitted) get bound to their most recent forward — the
-- one tied to the latest "In Review" row. If older "In Review" rows
-- need to point at older forwards, that's a v2 chronological-pairing
-- pass; v1 picks "latest" and accepts the rare mis-bind.
--
-- Idempotent: the WHERE EmailMessageId IS NULL clause skips already-
-- linked rows.
CREATE OR ALTER PROCEDURE ReconcileReviewEmailMessageLinks
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE r
       SET [EmailMessageId] = m.MatchedEmailMessageId
      FROM dbo.[Review] r
      INNER JOIN dbo.[ReviewStatus] rs ON rs.Id = r.[ReviewStatusId]
      INNER JOIN dbo.[Bill] b ON b.Id = r.[BillId]
      INNER JOIN dbo.[EmailMessage] src ON src.Id = b.[SourceEmailMessageId]
      CROSS APPLY (
          SELECT TOP 1 em.Id AS MatchedEmailMessageId
          FROM dbo.[EmailMessage] em
          WHERE em.[ConversationId] = src.[ConversationId]
            AND em.[Folder] = 'sentitems'
          ORDER BY em.[ReceivedDatetime] DESC
      ) m
      WHERE r.[EmailMessageId] IS NULL
        AND rs.[Name] = 'In Review';

    SELECT @@ROWCOUNT AS UpdatedRows;
END;
GO

-- Watermark for the polling loop. Returns MAX(ReceivedDatetime) for the
-- given (mailbox, folder), or NULL if no rows exist. Used to construct
-- the next Graph $filter clause `receivedDateTime ge <watermark>` so we
-- pick up only messages newer than what we have already ingested.
-- Comparison is case-insensitive on MailboxAddress to defend against
-- inconsistent casing on the configured value. Folder discriminates
-- inbox vs sentitems; each advances independently.
CREATE OR ALTER PROCEDURE ReadMaxReceivedDatetimeByMailbox
(
    @MailboxAddress NVARCHAR(320),
    @Folder NVARCHAR(50) = 'inbox'
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT MAX([ReceivedDatetime]) AS MaxReceivedDatetime
    FROM dbo.[EmailMessage]
    WHERE LOWER([MailboxAddress]) = LOWER(@MailboxAddress)
      AND [Folder] = @Folder;
END;
GO

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
    -- (Migration 004, 2026-05-27) Identity invariant: InternetMessageId
    -- (RFC 5322 Message-ID) is the stable primary key, not GraphMessageId.
    -- Microsoft Graph recycles GraphMessageId values in shared mailboxes
    -- (delete-restore cycles), so keying MERGE on it overwrites unrelated
    -- rows. Phase 1 of the 2026-05-27 data-corruption investigation
    -- traced 160 wrong-vendor Bills back to this. Hard-fail on missing
    -- IMID — every standard SMTP server stamps one and we have 0 NULL
    -- IMIDs in prod today.
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
            -- GraphMessageId is a MUTABLE secondary — Graph can recycle
            -- these and a later poll might see the same IMID under a
            -- different GraphMessageId. Always update to the latest.
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
            -- match: folder is part of the match key (immutable per
            -- row); status is owned by the agent claim/process workflow.
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

GO

CREATE OR ALTER PROCEDURE ReadEmailMessageById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [GraphMessageId],
        [InternetMessageId],
        [ConversationId],
        [MailboxAddress],
        [FromAddress],
        [FromName],
        [Subject],
        [BodyPreview],
        [BodyContent],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments],
        [AgentClassification],
        [AgentClassificationReason],
        [AgentDecidedAction],
        [AgentClassificationConfidence]
    FROM dbo.[EmailMessage]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadEmailMessageByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [GraphMessageId],
        [InternetMessageId],
        [ConversationId],
        [MailboxAddress],
        [FromAddress],
        [FromName],
        [Subject],
        [BodyPreview],
        [BodyContent],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments],
        [AgentClassification],
        [AgentClassificationReason],
        [AgentDecidedAction],
        [AgentClassificationConfidence]
    FROM dbo.[EmailMessage]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadEmailMessageByGraphMessageId
(
    @GraphMessageId NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [GraphMessageId],
        [InternetMessageId],
        [ConversationId],
        [MailboxAddress],
        [FromAddress],
        [FromName],
        [Subject],
        [BodyPreview],
        [BodyContent],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments],
        [AgentClassification],
        [AgentClassificationReason],
        [AgentDecidedAction],
        [AgentClassificationConfidence]
    FROM dbo.[EmailMessage]
    WHERE [GraphMessageId] = @GraphMessageId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateEmailMessageStatus
(
    @Id BIGINT,
    @ProcessingStatus NVARCHAR(50),
    @LastError NVARCHAR(MAX) = NULL,
    @AgentSessionId BIGINT = NULL,
    @AgentClassification NVARCHAR(50) = NULL,
    @AgentClassificationReason NVARCHAR(1024) = NULL,
    @AgentDecidedAction NVARCHAR(50) = NULL,
    @AgentClassificationConfidence DECIMAL(5,4) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- All optional params use NULL-preserving CASE WHEN guards: passing
    -- NULL leaves the existing column value alone, so a partial caller
    -- (e.g. ClaimNextPending re-using this for status only) doesn't wipe
    -- the agent's stamped classification.
    UPDATE dbo.[EmailMessage]
    SET
        [ModifiedDatetime] = @Now,
        [ProcessingStatus] = @ProcessingStatus,
        [LastError] = CASE WHEN @LastError IS NULL THEN [LastError] ELSE @LastError END,
        [AgentSessionId] = CASE WHEN @AgentSessionId IS NULL THEN [AgentSessionId] ELSE @AgentSessionId END,
        [AgentClassification] =
            CASE WHEN @AgentClassification IS NULL THEN [AgentClassification] ELSE @AgentClassification END,
        [AgentClassificationReason] =
            CASE WHEN @AgentClassificationReason IS NULL THEN [AgentClassificationReason] ELSE @AgentClassificationReason END,
        [AgentDecidedAction] =
            CASE WHEN @AgentDecidedAction IS NULL THEN [AgentDecidedAction] ELSE @AgentDecidedAction END,
        [AgentClassificationConfidence] =
            CASE WHEN @AgentClassificationConfidence IS NULL THEN [AgentClassificationConfidence] ELSE @AgentClassificationConfidence END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[ProcessingStatus],
        INSERTED.[LastError],
        INSERTED.[AgentSessionId],
        INSERTED.[AgentClassification],
        INSERTED.[AgentClassificationReason],
        INSERTED.[AgentDecidedAction],
        INSERTED.[AgentClassificationConfidence]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ClaimNextPendingEmailMessage
AS
BEGIN
    -- Atomically grab the oldest pending email and flip it to 'processing'.
    -- UPDLOCK + READPAST so concurrent worker ticks don't claim the same row.
    -- Always returns a result set (empty when nothing is pending).
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ClaimedId BIGINT;

    BEGIN TRANSACTION;

    ;WITH candidate AS (
        SELECT TOP 1 [Id]
        FROM dbo.[EmailMessage] WITH (UPDLOCK, READPAST, ROWLOCK)
        WHERE [ProcessingStatus] = 'pending'
        ORDER BY [ReceivedDatetime] ASC, [Id] ASC
    )
    UPDATE em
    SET em.[ProcessingStatus] = 'processing',
        em.[ModifiedDatetime] = @Now,
        @ClaimedId = em.[Id]
    FROM dbo.[EmailMessage] em
    INNER JOIN candidate c ON c.[Id] = em.[Id];

    COMMIT TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [GraphMessageId],
        [InternetMessageId],
        [ConversationId],
        [MailboxAddress],
        [FromAddress],
        [FromName],
        [Subject],
        [BodyPreview],
        [BodyContent],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments],
        [AgentClassification],
        [AgentClassificationReason],
        [AgentDecidedAction],
        [AgentClassificationConfidence]
    FROM dbo.[EmailMessage]
    WHERE [Id] = @ClaimedId;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadEmailMessagesPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @ProcessingStatus NVARCHAR(50) = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @SortBy NVARCHAR(50) = 'ReceivedDatetime',
    @SortDirection NVARCHAR(4) = 'DESC'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;

    DECLARE @SortColumn NVARCHAR(50);
    SET @SortColumn = CASE @SortBy
        WHEN 'ReceivedDatetime' THEN 'ReceivedDatetime'
        WHEN 'Subject' THEN 'Subject'
        WHEN 'FromAddress' THEN 'FromAddress'
        WHEN 'ProcessingStatus' THEN 'ProcessingStatus'
        ELSE 'ReceivedDatetime'
    END;

    DECLARE @SortDir NVARCHAR(4);
    SET @SortDir = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [GraphMessageId],
        [InternetMessageId],
        [ConversationId],
        [MailboxAddress],
        [FromAddress],
        [FromName],
        [Subject],
        [BodyPreview],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments],
        [AgentClassification],
        [AgentClassificationReason],
        [AgentDecidedAction],
        [AgentClassificationConfidence]
    FROM dbo.[EmailMessage]
    WHERE
        (@SearchTerm IS NULL OR
         [Subject] LIKE '%' + @SearchTerm + '%' OR
         [FromAddress] LIKE '%' + @SearchTerm + '%' OR
         [BodyPreview] LIKE '%' + @SearchTerm + '%')
        AND (@ProcessingStatus IS NULL OR [ProcessingStatus] = @ProcessingStatus)
        AND (@StartDate IS NULL OR [ReceivedDatetime] >= @StartDate)
        AND (@EndDate IS NULL OR [ReceivedDatetime] <= @EndDate)
    ORDER BY
        CASE WHEN @SortDir = 'ASC'  AND @SortColumn = 'ReceivedDatetime'  THEN [ReceivedDatetime] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ReceivedDatetime'  THEN [ReceivedDatetime] END DESC,
        CASE WHEN @SortDir = 'ASC'  AND @SortColumn = 'Subject'           THEN [Subject] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'Subject'           THEN [Subject] END DESC,
        CASE WHEN @SortDir = 'ASC'  AND @SortColumn = 'FromAddress'       THEN [FromAddress] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'FromAddress'       THEN [FromAddress] END DESC,
        CASE WHEN @SortDir = 'ASC'  AND @SortColumn = 'ProcessingStatus'  THEN [ProcessingStatus] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ProcessingStatus'  THEN [ProcessingStatus] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE CountEmailMessages
(
    @SearchTerm NVARCHAR(255) = NULL,
    @ProcessingStatus NVARCHAR(50) = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[EmailMessage]
    WHERE
        (@SearchTerm IS NULL OR
         [Subject] LIKE '%' + @SearchTerm + '%' OR
         [FromAddress] LIKE '%' + @SearchTerm + '%' OR
         [BodyPreview] LIKE '%' + @SearchTerm + '%')
        AND (@ProcessingStatus IS NULL OR [ProcessingStatus] = @ProcessingStatus)
        AND (@StartDate IS NULL OR [ReceivedDatetime] >= @StartDate)
        AND (@EndDate IS NULL OR [ReceivedDatetime] <= @EndDate);

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE DeleteEmailMessageById
(
    @Id BIGINT
)
AS
BEGIN
    -- SET NOCOUNT ON so the cascade DELETE's rowcount doesn't surface
    -- as a phantom result set ahead of the OUTPUT clause below.
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    -- Cascade: child EmailAttachment rows go away first
    DELETE FROM dbo.[EmailAttachment] WHERE [EmailMessageId] = @Id;

    DELETE FROM dbo.[EmailMessage]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[GraphMessageId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- ============================================================================
-- ReadEmailSenderHistory — sender-keyed prior-context lookup for the
-- email_specialist agent. Returns two result sets:
--
--   1. Aggregate counts row: total emails from this sender + breakdowns
--      by ProcessingStatus, AgentClassification, AgentDecidedAction,
--      plus committed-Bill / -Expense / -BillCredit counts.
--
--   2. Distinct Vendor rows associated with prior committed Bills from
--      this sender (one row per Vendor, with that vendor's bill count).
--
-- @ExcludePublicId optionally suppresses the current email from the
-- counts (keyed on PublicId so the agent can pass the same identifier
-- it received in its user_message — it doesn't know the internal Id).
-- ============================================================================

CREATE OR ALTER PROCEDURE ReadEmailSenderHistory
(
    @FromEmail NVARCHAR(320),
    @ExcludePublicId UNIQUEIDENTIFIER = NULL,
    @RecentLimit INT = 10
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Result set 1: aggregate counts
    SELECT
        COUNT(*)                                                                       AS PriorEmailsTotal,

        -- Workflow status counts
        SUM(CASE WHEN [ProcessingStatus] = 'pending'         THEN 1 ELSE 0 END)        AS StatusPending,
        SUM(CASE WHEN [ProcessingStatus] = 'processing'      THEN 1 ELSE 0 END)        AS StatusProcessing,
        SUM(CASE WHEN [ProcessingStatus] = 'extracted'       THEN 1 ELSE 0 END)        AS StatusExtracted,
        SUM(CASE WHEN [ProcessingStatus] = 'awaiting_review' THEN 1 ELSE 0 END)        AS StatusAwaitingReview,
        SUM(CASE WHEN [ProcessingStatus] = 'agent_complete'  THEN 1 ELSE 0 END)        AS StatusAgentComplete,
        SUM(CASE WHEN [ProcessingStatus] = 'irrelevant'      THEN 1 ELSE 0 END)        AS StatusIrrelevant,
        SUM(CASE WHEN [ProcessingStatus] = 'failed'          THEN 1 ELSE 0 END)        AS StatusFailed,

        -- Agent classification counts (controlled vocabulary)
        SUM(CASE WHEN [AgentClassification] = 'vendor_invoice'         THEN 1 ELSE 0 END) AS ClassVendorInvoice,
        SUM(CASE WHEN [AgentClassification] = 'vendor_credit_memo'     THEN 1 ELSE 0 END) AS ClassVendorCreditMemo,
        SUM(CASE WHEN [AgentClassification] = 'vendor_statement'       THEN 1 ELSE 0 END) AS ClassVendorStatement,
        SUM(CASE WHEN [AgentClassification] = 'vendor_expense_receipt' THEN 1 ELSE 0 END) AS ClassVendorExpenseReceipt,
        SUM(CASE WHEN [AgentClassification] = 'customer_payment'       THEN 1 ELSE 0 END) AS ClassCustomerPayment,
        SUM(CASE WHEN [AgentClassification] = 'customer_question'      THEN 1 ELSE 0 END) AS ClassCustomerQuestion,
        SUM(CASE WHEN [AgentClassification] = 'customer_dispute'       THEN 1 ELSE 0 END) AS ClassCustomerDispute,
        SUM(CASE WHEN [AgentClassification] = 'internal_reply'         THEN 1 ELSE 0 END) AS ClassInternalReply,
        SUM(CASE WHEN [AgentClassification] = 'internal_forward'       THEN 1 ELSE 0 END) AS ClassInternalForward,
        SUM(CASE WHEN [AgentClassification] = 'vendor_newsletter'        THEN 1 ELSE 0 END) AS ClassVendorNewsletter,
        SUM(CASE WHEN [AgentClassification] = 'contract_labor_timesheet' THEN 1 ELSE 0 END) AS ClassContractLaborTimesheet,
        SUM(CASE WHEN [AgentClassification] = 'non_actionable'           THEN 1 ELSE 0 END) AS ClassNonActionable,
        SUM(CASE WHEN [AgentClassification] = 'unknown'                THEN 1 ELSE 0 END) AS ClassUnknown,
        SUM(CASE WHEN [AgentClassification] IS NULL                    THEN 1 ELSE 0 END) AS ClassUnclassified,

        -- Agent action counts
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_bill_specialist'              THEN 1 ELSE 0 END) AS ActionDelegatedBill,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_bill_credit_specialist'       THEN 1 ELSE 0 END) AS ActionDelegatedBillCredit,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_expense_specialist'           THEN 1 ELSE 0 END) AS ActionDelegatedExpense,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_contract_labor_specialist'    THEN 1 ELSE 0 END) AS ActionDelegatedContractLabor,
        SUM(CASE WHEN [AgentDecidedAction] = 'flagged_needs_review'                      THEN 1 ELSE 0 END) AS ActionFlaggedReview,
        SUM(CASE WHEN [AgentDecidedAction] = 'marked_irrelevant'                         THEN 1 ELSE 0 END) AS ActionMarkedIrrelevant,
        SUM(CASE WHEN [AgentDecidedAction] = 'marked_processed'                          THEN 1 ELSE 0 END) AS ActionMarkedProcessed,
        SUM(CASE WHEN [AgentDecidedAction] IS NULL                                        THEN 1 ELSE 0 END) AS ActionUnset,

        -- Committed-entity counts (cross joins via SourceEmailMessageId)
        ISNULL((SELECT COUNT(*) FROM dbo.[Bill] b
                INNER JOIN dbo.[EmailMessage] em ON em.[Id] = b.[SourceEmailMessageId]
                WHERE em.[FromAddress] = @FromEmail), 0)         AS PriorBillsCommitted,
        ISNULL((SELECT COUNT(*) FROM dbo.[Expense] e
                INNER JOIN dbo.[EmailMessage] em ON em.[Id] = e.[SourceEmailMessageId]
                WHERE em.[FromAddress] = @FromEmail), 0)         AS PriorExpensesCommitted,
        ISNULL((SELECT COUNT(*) FROM dbo.[BillCredit] bc
                INNER JOIN dbo.[EmailMessage] em ON em.[Id] = bc.[SourceEmailMessageId]
                WHERE em.[FromAddress] = @FromEmail), 0)         AS PriorBillCreditsCommitted
    FROM dbo.[EmailMessage]
    WHERE [FromAddress] = @FromEmail
      AND (@ExcludePublicId IS NULL OR [PublicId] <> @ExcludePublicId);

    -- Result set 2: distinct Vendors associated with prior committed Bills
    -- from this sender. Empty if no Bills have been committed yet.
    SELECT
        v.[Id]                                  AS VendorId,
        CAST(v.[PublicId] AS NVARCHAR(36))      AS VendorPublicId,
        v.[Name]                                AS VendorName,
        COUNT(b.[Id])                           AS BillCount
    FROM dbo.[Vendor] v
    INNER JOIN dbo.[Bill] b           ON b.[VendorId]              = v.[Id]
    INNER JOIN dbo.[EmailMessage] em  ON em.[Id]                   = b.[SourceEmailMessageId]
    WHERE em.[FromAddress] = @FromEmail
    GROUP BY v.[Id], v.[PublicId], v.[Name]
    ORDER BY VendorName;

    -- Result set 3: most recent N classified prior emails from this sender.
    -- The agent reads this to see WHAT was decided + WHY on similar prior
    -- emails, not just aggregate counts. Cuts down on classification
    -- mistakes when a sender's emails have varied over time (e.g. vendor
    -- that switched from invoices to statements). Ordered newest-first so
    -- the most relevant prior decision is at the top.
    SELECT TOP (@RecentLimit)
        CAST([PublicId] AS NVARCHAR(36))                 AS EmailPublicId,
        [Subject]                                        AS Subject,
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120)    AS ReceivedDatetime,
        [AgentClassification]                            AS Classification,
        [AgentClassificationReason]                      AS ClassificationReason,
        [AgentDecidedAction]                             AS DecidedAction
    FROM dbo.[EmailMessage]
    WHERE [FromAddress] = @FromEmail
      AND (@ExcludePublicId IS NULL OR [PublicId] <> @ExcludePublicId)
      AND [AgentClassification] IS NOT NULL
    ORDER BY [ReceivedDatetime] DESC;
END;
GO
GO

-- Recovery sproc: resets EmailMessage rows that are stuck in 'processing'
-- with no AgentSessionId stamped. See migrations/001_recovery_processing_reset.sql
-- for the full background.
CREATE OR ALTER PROCEDURE dbo.RecoverStuckProcessingEmailMessages
    @StaleAfterMinutes INT = 10,
    @MaxResets INT = 3
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Cutoff DATETIME2(3) = DATEADD(MINUTE, -@StaleAfterMinutes, @Now);
    DECLARE @ResetCount INT = 0;
    DECLARE @FailedCount INT = 0;

    UPDATE dbo.[EmailMessage]
    SET [ProcessingStatus] = 'pending',
        [ProcessingResetCount] = [ProcessingResetCount] + 1,
        [LastError] = CONCAT(
            'auto-reset by recovery cron (reset #',
            [ProcessingResetCount] + 1,
            ', stale ', DATEDIFF(MINUTE, [ModifiedDatetime], @Now), ' min)'
        ),
        [ModifiedDatetime] = @Now
    WHERE [ProcessingStatus] = 'processing'
      AND [AgentSessionId] IS NULL
      AND [ModifiedDatetime] < @Cutoff
      AND [ProcessingResetCount] < @MaxResets;
    SET @ResetCount = @@ROWCOUNT;

    UPDATE dbo.[EmailMessage]
    SET [ProcessingStatus] = 'failed',
        [LastError] = CONCAT(
            'auto-failed after ', [ProcessingResetCount], ' resets ',
            '(stuck in processing without AgentSessionId)'
        ),
        [ModifiedDatetime] = @Now
    WHERE [ProcessingStatus] = 'processing'
      AND [AgentSessionId] IS NULL
      AND [ModifiedDatetime] < @Cutoff
      AND [ProcessingResetCount] >= @MaxResets;
    SET @FailedCount = @@ROWCOUNT;

    SELECT @ResetCount AS ResetCount, @FailedCount AS FailedCount;
END;
GO
