-- =============================================================================
-- dbo.InboxRecord — Add InternetMessageId and ConversationId
-- Non-destructive ALTER following the existing classification columns pattern.
-- These fields are required for EmailThread dedup and thread chain resolution.
--
-- InternetMessageId: RFC 2822 Message-ID header — the thread dedup key.
--                    Links reply/forward emails back to the originating message.
-- ConversationId:    Microsoft Graph conversation grouping ID — useful fallback
--                    for thread detection when InternetMessageId is absent.
-- =============================================================================

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.InboxRecord')
    AND name = 'InternetMessageId'
)
    ALTER TABLE dbo.InboxRecord
        ADD InternetMessageId NVARCHAR(500) NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.InboxRecord')
    AND name = 'ConversationId'
)
    ALTER TABLE dbo.InboxRecord
        ADD ConversationId NVARCHAR(500) NULL;
GO

-- Index on InternetMessageId — used by EmailThread dedup lookup
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_InboxRecord_InternetMessageId'
    AND object_id = OBJECT_ID('dbo.InboxRecord')
)
    CREATE INDEX IX_InboxRecord_InternetMessageId
        ON dbo.InboxRecord (InternetMessageId)
        WHERE InternetMessageId IS NOT NULL;
GO

-- Index on ConversationId — used for Graph-level thread grouping
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_InboxRecord_ConversationId'
    AND object_id = OBJECT_ID('dbo.InboxRecord')
)
    CREATE INDEX IX_InboxRecord_ConversationId
        ON dbo.InboxRecord (ConversationId)
        WHERE ConversationId IS NOT NULL;
GO

-- =============================================================================
-- Update UpsertInboxRecord to include the two new columns.
-- NULL input preserves existing value via COALESCE guard — consistent with
-- all other nullable fields in this sproc.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.UpsertInboxRecord
    @MessageId                  NVARCHAR(500),
    @Status                     VARCHAR(50),
    @SubmittedToEmail           NVARCHAR(500)   = NULL,
    @SubmittedAt                DATETIME2(3)    = NULL,
    @ProcessedAt                DATETIME2(3)    = NULL,
    @RecordType                 VARCHAR(100)    = NULL,
    @RecordPublicId             UNIQUEIDENTIFIER = NULL,
    @ClassificationType         VARCHAR(100)    = NULL,
    @ClassificationConfidence   DECIMAL(5,4)    = NULL,
    @ClassificationSignals      NVARCHAR(MAX)   = NULL,
    @ClassifiedAt               DATETIME2(3)    = NULL,
    @UserOverrideType           VARCHAR(100)    = NULL,
    @Subject                    NVARCHAR(500)   = NULL,
    @FromEmail                  NVARCHAR(500)   = NULL,
    @FromName                   NVARCHAR(500)   = NULL,
    @HasAttachments             BIT             = NULL,
    @ProcessedVia               VARCHAR(100)    = NULL,
    @InternetMessageId          NVARCHAR(500)   = NULL,
    @ConversationId             NVARCHAR(500)   = NULL
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dbo.InboxRecord WITH (HOLDLOCK) AS target
    USING (SELECT @MessageId AS MessageId) AS source
        ON target.MessageId = source.MessageId

    WHEN MATCHED THEN
        UPDATE SET
            Status                  = @Status,
            SubmittedToEmail        = COALESCE(@SubmittedToEmail,       target.SubmittedToEmail),
            SubmittedAt             = COALESCE(@SubmittedAt,            target.SubmittedAt),
            ProcessedAt             = COALESCE(@ProcessedAt,            target.ProcessedAt),
            RecordType              = COALESCE(@RecordType,             target.RecordType),
            RecordPublicId          = COALESCE(@RecordPublicId,         target.RecordPublicId),
            ClassificationType      = COALESCE(@ClassificationType,     target.ClassificationType),
            ClassificationConfidence= COALESCE(@ClassificationConfidence, target.ClassificationConfidence),
            ClassificationSignals   = COALESCE(@ClassificationSignals,  target.ClassificationSignals),
            ClassifiedAt            = COALESCE(@ClassifiedAt,           target.ClassifiedAt),
            UserOverrideType        = COALESCE(@UserOverrideType,       target.UserOverrideType),
            Subject                 = COALESCE(@Subject,                target.Subject),
            FromEmail               = COALESCE(@FromEmail,              target.FromEmail),
            FromName                = COALESCE(@FromName,               target.FromName),
            HasAttachments          = COALESCE(@HasAttachments,         target.HasAttachments),
            ProcessedVia            = COALESCE(@ProcessedVia,           target.ProcessedVia),
            InternetMessageId       = COALESCE(@InternetMessageId,      target.InternetMessageId),
            ConversationId          = COALESCE(@ConversationId,         target.ConversationId),
            ModifiedDatetime        = SYSUTCDATETIME()

    WHEN NOT MATCHED THEN
        INSERT (
            MessageId,
            Status,
            SubmittedToEmail,
            SubmittedAt,
            ProcessedAt,
            RecordType,
            RecordPublicId,
            ClassificationType,
            ClassificationConfidence,
            ClassificationSignals,
            ClassifiedAt,
            UserOverrideType,
            Subject,
            FromEmail,
            FromName,
            HasAttachments,
            ProcessedVia,
            InternetMessageId,
            ConversationId
        )
        VALUES (
            @MessageId,
            @Status,
            @SubmittedToEmail,
            @SubmittedAt,
            @ProcessedAt,
            @RecordType,
            @RecordPublicId,
            @ClassificationType,
            @ClassificationConfidence,
            @ClassificationSignals,
            @ClassifiedAt,
            @UserOverrideType,
            @Subject,
            @FromEmail,
            @FromName,
            @HasAttachments,
            @ProcessedVia,
            @InternetMessageId,
            @ConversationId
        );

    SELECT
        Id,
        PublicId,
        CONVERT(VARCHAR(MAX), RowVersion, 2)    AS RowVersion,
        MessageId,
        Status,
        SubmittedToEmail,
        CONVERT(VARCHAR(23), SubmittedAt, 126)  AS SubmittedAt,
        CONVERT(VARCHAR(23), ProcessedAt, 126)  AS ProcessedAt,
        RecordType,
        RecordPublicId,
        ClassificationType,
        ClassificationConfidence,
        ClassificationSignals,
        CONVERT(VARCHAR(23), ClassifiedAt, 126) AS ClassifiedAt,
        UserOverrideType,
        Subject,
        FromEmail,
        FromName,
        HasAttachments,
        ProcessedVia,
        InternetMessageId,
        ConversationId,
        CONVERT(VARCHAR(23), CreatedDatetime, 126)  AS CreatedDatetime,
        CONVERT(VARCHAR(23), ModifiedDatetime, 126) AS ModifiedDatetime
    FROM dbo.InboxRecord
    WHERE MessageId = @MessageId;
END
GO
