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
    [GraphMessageId] NVARCHAR(255) NOT NULL,
    [InternetMessageId] NVARCHAR(255) NULL,
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
    CONSTRAINT [UQ_EmailMessage_GraphMessageId] UNIQUE ([GraphMessageId])
);
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

GO

CREATE OR ALTER PROCEDURE UpsertEmailMessage
(
    @GraphMessageId NVARCHAR(255),
    @InternetMessageId NVARCHAR(255) = NULL,
    @ConversationId NVARCHAR(255) = NULL,
    @MailboxAddress NVARCHAR(320),
    @FromAddress NVARCHAR(320) = NULL,
    @FromName NVARCHAR(255) = NULL,
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
             [ConversationId], [MailboxAddress], [FromAddress], [FromName], [Subject],
             [BodyPreview], [BodyContent], [BodyContentType], [ReceivedDatetime],
             [ProcessingStatus], [WebLink], [HasAttachments])
        VALUES
            (@Now, @Now, @GraphMessageId, @InternetMessageId,
             @ConversationId, @MailboxAddress, @FromAddress, @FromName, @Subject,
             @BodyPreview, @BodyContent, @BodyContentType, @ReceivedDatetime,
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
        [HasAttachments]
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
        [HasAttachments]
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
        [HasAttachments]
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
    @AgentSessionId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[EmailMessage]
    SET
        [ModifiedDatetime] = @Now,
        [ProcessingStatus] = @ProcessingStatus,
        [LastError] = CASE WHEN @LastError IS NULL THEN [LastError] ELSE @LastError END,
        [AgentSessionId] = CASE WHEN @AgentSessionId IS NULL THEN [AgentSessionId] ELSE @AgentSessionId END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[ProcessingStatus],
        INSERTED.[LastError],
        INSERTED.[AgentSessionId]
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
        [HasAttachments]
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
        [HasAttachments]
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
