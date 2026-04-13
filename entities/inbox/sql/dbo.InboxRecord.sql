-- =============================================================================
-- InboxRecord
-- =============================================================================
-- Tracks the triage status of each inbox message as it moves through the
-- accounting workflow: New → Pending Review → Processed (or Skipped).
-- One row per MS Graph message ID (UNIQUE constraint enforced).
--
-- Also persists AI classification data and user overrides to build a labeled
-- dataset for future ML model training.
-- =============================================================================

-- ── Table ────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.InboxRecord', 'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[InboxRecord]
    (
        [Id]               BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId]         UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion]       ROWVERSION NOT NULL,
        [CreatedDatetime]  DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        [ModifiedDatetime] DATETIME2(3) NULL,

        -- The MS Graph immutable message ID
        [MessageId]        NVARCHAR(500) NOT NULL,

        -- Workflow status
        [Status]           NVARCHAR(50)  NOT NULL DEFAULT 'new',

        -- Submit-for-review metadata
        [SubmittedToEmail] NVARCHAR(320) NULL,
        [SubmittedAt]      DATETIME2(3)  NULL,

        -- Process metadata
        [ProcessedAt]      DATETIME2(3)  NULL,
        [RecordType]       NVARCHAR(50)  NULL,   -- bill | expense | credit
        [RecordPublicId]   NVARCHAR(100) NULL,   -- public_id of created entity

        CONSTRAINT [UQ_InboxRecord_PublicId]  UNIQUE ([PublicId]),
        CONSTRAINT [UQ_InboxRecord_MessageId] UNIQUE ([MessageId]),
        CONSTRAINT [CK_InboxRecord_Status]    CHECK  ([Status] IN ('new', 'pending_review', 'processed', 'skipped'))
    );
END
GO

-- ── Classification columns (non-destructive ALTER) ─────────────────────────

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.InboxRecord') AND name = 'ClassificationType')
BEGIN
    ALTER TABLE [dbo].[InboxRecord] ADD
        [ClassificationType]       NVARCHAR(50)   NULL,   -- bill | expense | vendor_credit | inquiry | statement | unknown
        [ClassificationConfidence] FLOAT          NULL,   -- 0.0 - 1.0
        [ClassificationSignals]    NVARCHAR(MAX)  NULL,   -- JSON array of signal strings
        [ClassifiedAt]             DATETIME2(3)   NULL,   -- when first classified
        [UserOverrideType]         NVARCHAR(50)   NULL,   -- set when outcome type != classification type
        [Subject]                  NVARCHAR(500)  NULL,   -- feature: email subject
        [FromEmail]                NVARCHAR(320)  NULL,   -- feature: sender email
        [FromName]                 NVARCHAR(320)  NULL,   -- feature: sender name
        [HasAttachments]           BIT            NULL,   -- feature: had extractable attachments
        [ProcessedVia]             NVARCHAR(20)   NULL;   -- 'web' | 'copilot' — which interface was used
END
GO

-- ── Indexes ──────────────────────────────────────────────────────────────────

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InboxRecord_MessageId')
BEGIN
    CREATE INDEX [IX_InboxRecord_MessageId] ON [dbo].[InboxRecord] ([MessageId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InboxRecord_Status')
BEGIN
    CREATE INDEX [IX_InboxRecord_Status] ON [dbo].[InboxRecord] ([Status]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InboxRecord_CreatedDatetime')
BEGIN
    CREATE INDEX [IX_InboxRecord_CreatedDatetime] ON [dbo].[InboxRecord] ([CreatedDatetime] DESC);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InboxRecord_ClassificationType')
BEGIN
    CREATE INDEX [IX_InboxRecord_ClassificationType] ON [dbo].[InboxRecord] ([ClassificationType]);
END
GO

-- ── Migration: Change RecordPublicId from NVARCHAR(100) to UNIQUEIDENTIFIER ─
IF EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'InboxRecord' AND COLUMN_NAME = 'RecordPublicId' AND DATA_TYPE = 'nvarchar')
BEGIN
    ALTER TABLE [dbo].[InboxRecord] ALTER COLUMN [RecordPublicId] UNIQUEIDENTIFIER NULL;
END
GO

-- ── UpsertInboxRecord ────────────────────────────────────────────────────────
-- INSERT a new record or UPDATE the existing one for a given MessageId.
-- Fields with NULL input keep their current DB values on UPDATE.

CREATE OR ALTER PROCEDURE [dbo].[UpsertInboxRecord]
    @MessageId                NVARCHAR(500),
    @Status                   NVARCHAR(50),
    @SubmittedToEmail         NVARCHAR(320) = NULL,
    @SubmittedAt              DATETIME2(3)  = NULL,
    @ProcessedAt              DATETIME2(3)  = NULL,
    @RecordType               NVARCHAR(50)  = NULL,
    @RecordPublicId           NVARCHAR(100) = NULL,
    @ClassificationType       NVARCHAR(50)  = NULL,
    @ClassificationConfidence FLOAT         = NULL,
    @ClassificationSignals    NVARCHAR(MAX) = NULL,
    @ClassifiedAt             DATETIME2(3)  = NULL,
    @UserOverrideType         NVARCHAR(50)  = NULL,
    @Subject                  NVARCHAR(500) = NULL,
    @FromEmail                NVARCHAR(320) = NULL,
    @FromName                 NVARCHAR(320) = NULL,
    @HasAttachments           BIT           = NULL,
    @ProcessedVia             NVARCHAR(20)  = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE [dbo].[InboxRecord] AS target
    USING (SELECT @MessageId AS MessageId) AS source
    ON target.[MessageId] = source.[MessageId]

    WHEN MATCHED THEN
        UPDATE SET
            [ModifiedDatetime]         = @Now,
            [Status]                   = @Status,
            -- Only overwrite nullable fields when caller provides a value
            [SubmittedToEmail]         = COALESCE(@SubmittedToEmail,         target.[SubmittedToEmail]),
            [SubmittedAt]              = COALESCE(@SubmittedAt,              target.[SubmittedAt]),
            [ProcessedAt]              = COALESCE(@ProcessedAt,              target.[ProcessedAt]),
            [RecordType]               = COALESCE(@RecordType,              target.[RecordType]),
            [RecordPublicId]           = COALESCE(@RecordPublicId,          target.[RecordPublicId]),
            [ClassificationType]       = COALESCE(@ClassificationType,      target.[ClassificationType]),
            [ClassificationConfidence] = COALESCE(@ClassificationConfidence, target.[ClassificationConfidence]),
            [ClassificationSignals]    = COALESCE(@ClassificationSignals,   target.[ClassificationSignals]),
            [ClassifiedAt]             = COALESCE(@ClassifiedAt,            target.[ClassifiedAt]),
            [UserOverrideType]         = COALESCE(@UserOverrideType,        target.[UserOverrideType]),
            [Subject]                  = COALESCE(@Subject,                 target.[Subject]),
            [FromEmail]                = COALESCE(@FromEmail,               target.[FromEmail]),
            [FromName]                 = COALESCE(@FromName,                target.[FromName]),
            [HasAttachments]           = COALESCE(@HasAttachments,          target.[HasAttachments]),
            [ProcessedVia]             = COALESCE(@ProcessedVia,            target.[ProcessedVia])

    WHEN NOT MATCHED THEN
        INSERT (
            [CreatedDatetime], [ModifiedDatetime],
            [MessageId], [Status],
            [SubmittedToEmail], [SubmittedAt],
            [ProcessedAt], [RecordType], [RecordPublicId],
            [ClassificationType], [ClassificationConfidence], [ClassificationSignals], [ClassifiedAt],
            [UserOverrideType],
            [Subject], [FromEmail], [FromName], [HasAttachments],
            [ProcessedVia]
        )
        VALUES (
            @Now, @Now,
            @MessageId, @Status,
            @SubmittedToEmail, @SubmittedAt,
            @ProcessedAt, @RecordType, @RecordPublicId,
            @ClassificationType, @ClassificationConfidence, @ClassificationSignals, @ClassifiedAt,
            @UserOverrideType,
            @Subject, @FromEmail, @FromName, @HasAttachments,
            @ProcessedVia
        );

    -- Return the upserted row
    SELECT
        [Id],
        CAST([PublicId]  AS NVARCHAR(36))  AS PublicId,
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS ModifiedDatetime,
        [MessageId],
        [Status],
        [SubmittedToEmail],
        CONVERT(VARCHAR(19), [SubmittedAt],  126) AS SubmittedAt,
        CONVERT(VARCHAR(19), [ProcessedAt],  126) AS ProcessedAt,
        [RecordType],
        [RecordPublicId],
        [ClassificationType],
        [ClassificationConfidence],
        [ClassificationSignals],
        CONVERT(VARCHAR(19), [ClassifiedAt], 120) AS ClassifiedAt,
        [UserOverrideType],
        [Subject],
        [FromEmail],
        [FromName],
        [HasAttachments],
        [ProcessedVia]
    FROM [dbo].[InboxRecord]
    WHERE [MessageId] = @MessageId;
END
GO

-- ── ReadInboxRecordByMessageId ───────────────────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[ReadInboxRecordByMessageId]
    @MessageId NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [Id],
        CAST([PublicId]  AS NVARCHAR(36))  AS PublicId,
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS ModifiedDatetime,
        [MessageId],
        [Status],
        [SubmittedToEmail],
        CONVERT(VARCHAR(19), [SubmittedAt],  126) AS SubmittedAt,
        CONVERT(VARCHAR(19), [ProcessedAt],  126) AS ProcessedAt,
        [RecordType],
        [RecordPublicId],
        [ClassificationType],
        [ClassificationConfidence],
        [ClassificationSignals],
        CONVERT(VARCHAR(19), [ClassifiedAt], 120) AS ClassifiedAt,
        [UserOverrideType],
        [Subject],
        [FromEmail],
        [FromName],
        [HasAttachments],
        [ProcessedVia]
    FROM [dbo].[InboxRecord]
    WHERE [MessageId] = @MessageId;
END
GO

-- ── ReadInboxRecordByRecordPublicId ──────────────────────────────────────────
-- Look up the inbox record that created a given bill/expense/credit.

CREATE OR ALTER PROCEDURE [dbo].[ReadInboxRecordByRecordPublicId]
    @RecordPublicId NVARCHAR(100)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1
        [Id],
        CAST([PublicId]  AS NVARCHAR(36))  AS PublicId,
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS ModifiedDatetime,
        [MessageId],
        [Status],
        [SubmittedToEmail],
        CONVERT(VARCHAR(19), [SubmittedAt],  126) AS SubmittedAt,
        CONVERT(VARCHAR(19), [ProcessedAt],  126) AS ProcessedAt,
        [RecordType],
        [RecordPublicId],
        [ClassificationType],
        [ClassificationConfidence],
        [ClassificationSignals],
        CONVERT(VARCHAR(19), [ClassifiedAt], 120) AS ClassifiedAt,
        [UserOverrideType],
        [Subject],
        [FromEmail],
        [FromName],
        [HasAttachments],
        [ProcessedVia]
    FROM [dbo].[InboxRecord]
    WHERE [RecordPublicId] = @RecordPublicId;
END
GO

-- ── ReadInboxRecordsByMessageIds ─────────────────────────────────────────────
-- Batch lookup: accepts a comma-separated list of MessageIds.
-- Used to enrich the inbox list view without N+1 queries.

CREATE OR ALTER PROCEDURE [dbo].[ReadInboxRecordsByMessageIds]
    @MessageIds NVARCHAR(MAX)   -- comma-separated message IDs
AS
BEGIN
    SET NOCOUNT ON;

    -- Split the comma-separated list into a temp table
    CREATE TABLE #Ids ([MessageId] NVARCHAR(500));
    INSERT INTO #Ids ([MessageId])
    SELECT LTRIM(RTRIM(value))
    FROM   STRING_SPLIT(@MessageIds, ',')
    WHERE  LTRIM(RTRIM(value)) <> '';

    SELECT
        ir.[Id],
        CAST(ir.[PublicId]  AS NVARCHAR(36))  AS PublicId,
        ir.[RowVersion],
        CONVERT(VARCHAR(19), ir.[CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(VARCHAR(19), ir.[ModifiedDatetime], 120) AS ModifiedDatetime,
        ir.[MessageId],
        ir.[Status],
        ir.[SubmittedToEmail],
        CONVERT(VARCHAR(19), ir.[SubmittedAt],  126) AS SubmittedAt,
        CONVERT(VARCHAR(19), ir.[ProcessedAt],  126) AS ProcessedAt,
        ir.[RecordType],
        ir.[RecordPublicId],
        ir.[ClassificationType],
        ir.[ClassificationConfidence],
        ir.[ClassificationSignals],
        CONVERT(VARCHAR(19), ir.[ClassifiedAt], 120) AS ClassifiedAt,
        ir.[UserOverrideType],
        ir.[Subject],
        ir.[FromEmail],
        ir.[FromName],
        ir.[HasAttachments],
        ir.[ProcessedVia]
    FROM [dbo].[InboxRecord] ir
    INNER JOIN #Ids t ON ir.[MessageId] = t.[MessageId];

    DROP TABLE #Ids;
END
GO

-- ── ReadInboxRecordsBySender ────────────────────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[ReadInboxRecordsBySender]
    @FromEmail NVARCHAR(320),
    @Limit INT = 10
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (@Limit)
        [Id],
        CAST([PublicId]  AS NVARCHAR(36))  AS PublicId,
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS ModifiedDatetime,
        [MessageId],
        [Status],
        [SubmittedToEmail],
        CONVERT(VARCHAR(19), [SubmittedAt],  126) AS SubmittedAt,
        CONVERT(VARCHAR(19), [ProcessedAt],  126) AS ProcessedAt,
        [RecordType],
        [RecordPublicId],
        [ClassificationType],
        [ClassificationConfidence],
        [ClassificationSignals],
        CONVERT(VARCHAR(19), [ClassifiedAt], 120) AS ClassifiedAt,
        [UserOverrideType],
        [Subject],
        [FromEmail],
        [FromName],
        [HasAttachments],
        [ProcessedVia],
        [InternetMessageId],
        [ConversationId]
    FROM [dbo].[InboxRecord]
    WHERE [FromEmail] = @FromEmail
      AND [ClassificationType] IS NOT NULL
    ORDER BY [CreatedDatetime] DESC;
END
GO

-- ── ReadInboxRecordsByConversationId ────────────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[ReadInboxRecordsByConversationId]
    @ConversationId NVARCHAR(500),
    @Limit INT = 10
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (@Limit)
        [Id],
        CAST([PublicId]  AS NVARCHAR(36))  AS PublicId,
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS ModifiedDatetime,
        [MessageId],
        [Status],
        [SubmittedToEmail],
        CONVERT(VARCHAR(19), [SubmittedAt],  126) AS SubmittedAt,
        CONVERT(VARCHAR(19), [ProcessedAt],  126) AS ProcessedAt,
        [RecordType],
        [RecordPublicId],
        [ClassificationType],
        [ClassificationConfidence],
        [ClassificationSignals],
        CONVERT(VARCHAR(19), [ClassifiedAt], 120) AS ClassifiedAt,
        [UserOverrideType],
        [Subject],
        [FromEmail],
        [FromName],
        [HasAttachments],
        [ProcessedVia],
        [InternetMessageId],
        [ConversationId]
    FROM [dbo].[InboxRecord]
    WHERE [ConversationId] = @ConversationId
      AND [ClassificationType] IS NOT NULL
    ORDER BY [CreatedDatetime] DESC;
END
GO

-- ── ReadInboxRecordsAwaitingReply ───────────────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[ReadInboxRecordsAwaitingReply]
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        ir.[Id],
        CAST(ir.[PublicId]  AS NVARCHAR(36))  AS PublicId,
        ir.[RowVersion],
        CONVERT(VARCHAR(19), ir.[CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(VARCHAR(19), ir.[ModifiedDatetime], 120) AS ModifiedDatetime,
        ir.[MessageId],
        ir.[Status],
        ir.[SubmittedToEmail],
        CONVERT(VARCHAR(19), ir.[SubmittedAt],  126) AS SubmittedAt,
        CONVERT(VARCHAR(19), ir.[ProcessedAt],  126) AS ProcessedAt,
        ir.[RecordType],
        ir.[RecordPublicId],
        ir.[ClassificationType],
        ir.[ClassificationConfidence],
        ir.[ClassificationSignals],
        CONVERT(VARCHAR(19), ir.[ClassifiedAt], 120) AS ClassifiedAt,
        ir.[UserOverrideType],
        ir.[Subject],
        ir.[FromEmail],
        ir.[FromName],
        ir.[HasAttachments],
        ir.[ProcessedVia],
        ir.[InternetMessageId],
        ir.[ConversationId]
    FROM [dbo].[InboxRecord] ir
    JOIN [dbo].[Bill] b ON b.[PublicId] = ir.[RecordPublicId]
    JOIN [dbo].[ReviewEntry] re ON re.[BillId] = b.[Id]
    JOIN [dbo].[ReviewStatus] rs ON rs.[Id] = re.[ReviewStatusId]
    WHERE rs.[Name] = 'In Review'
      AND ir.[ConversationId] IS NOT NULL
      AND re.[Id] = (
          SELECT TOP 1 re2.[Id]
          FROM [dbo].[ReviewEntry] re2
          WHERE re2.[BillId] = b.[Id]
          ORDER BY re2.[CreatedDatetime] DESC
      );
END
GO
