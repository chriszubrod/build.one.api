-- Additive migration: add ToRecipients + CcRecipients (JSON arrays) to
-- dbo.EmailMessage and extend UpsertEmailMessage to accept them.
--
-- The columns store JSON like:
--   [{"email": "...", "name": "..."}, ...]
-- Storing as NVARCHAR(MAX) (not the JSON type) for cross-version
-- portability; SQL Server's JSON_QUERY / OPENJSON still work over it
-- when we want to query.
--
-- Idempotent: the ADD COLUMN guards on sys.columns; the sproc is
-- CREATE OR ALTER.

GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'ToRecipients' AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [ToRecipients] NVARCHAR(MAX) NULL;
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'CcRecipients' AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [CcRecipients] NVARCHAR(MAX) NULL;
END
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
    @HasAttachments BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[EmailMessage] AS target
    USING (SELECT @GraphMessageId AS GraphMessageId) AS source
    ON target.[GraphMessageId] = source.GraphMessageId
    WHEN MATCHED THEN
        UPDATE SET
            [ModifiedDatetime] = @Now,
            [InternetMessageId] = @InternetMessageId,
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
    WHEN NOT MATCHED THEN
        INSERT
            ([CreatedDatetime], [ModifiedDatetime], [GraphMessageId], [InternetMessageId],
             [ConversationId], [MailboxAddress], [FromAddress], [FromName],
             [ToRecipients], [CcRecipients],
             [Subject], [BodyPreview], [BodyContent], [BodyContentType], [ReceivedDatetime],
             [ProcessingStatus], [WebLink], [HasAttachments])
        VALUES
            (@Now, @Now, @GraphMessageId, @InternetMessageId,
             @ConversationId, @MailboxAddress, @FromAddress, @FromName,
             @ToRecipients, @CcRecipients,
             @Subject, @BodyPreview, @BodyContent, @BodyContentType, @ReceivedDatetime,
             'pending', @WebLink, @HasAttachments)
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
        INSERTED.[ToRecipients],
        INSERTED.[CcRecipients],
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

-- Update the read sprocs to include the new columns so existing API
-- consumers see them. The remaining sprocs (Update*Status,
-- ClaimNextPending, paginated read, count, delete) don't need touching —
-- they either don't return body data or already SELECT *.
CREATE OR ALTER PROCEDURE ReadEmailMessageById
(
    @Id BIGINT
)
AS
BEGIN
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
        [ToRecipients],
        [CcRecipients],
        [Subject],
        [BodyPreview],
        [BodyContent],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments]
    FROM dbo.[EmailMessage]
    WHERE [Id] = @Id;
END;
GO

CREATE OR ALTER PROCEDURE ReadEmailMessageByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
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
        [ToRecipients],
        [CcRecipients],
        [Subject],
        [BodyPreview],
        [BodyContent],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments]
    FROM dbo.[EmailMessage]
    WHERE [PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE ReadEmailMessageByGraphMessageId
(
    @GraphMessageId NVARCHAR(255)
)
AS
BEGIN
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
        [ToRecipients],
        [CcRecipients],
        [Subject],
        [BodyPreview],
        [BodyContent],
        [BodyContentType],
        CONVERT(VARCHAR(19), [ReceivedDatetime], 120) AS [ReceivedDatetime],
        [ProcessingStatus],
        [LastError],
        [AgentSessionId],
        [WebLink],
        [HasAttachments]
    FROM dbo.[EmailMessage]
    WHERE [GraphMessageId] = @GraphMessageId;
END;
GO
