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
    -- Agent classification stamp (set by mark_email_outcome). Captures
    -- the email_specialist's *semantic* decision (what kind of doc this
    -- was + what action it took) — independent of ProcessingStatus
    -- (which tracks workflow). Powers search_email_sender_history so
    -- prior emails inform the next classification.
    [AgentClassification] NVARCHAR(50) NULL,
    [AgentClassificationReason] NVARCHAR(1024) NULL,
    [AgentDecidedAction] NVARCHAR(50) NULL,
    [AgentClassificationConfidence] DECIMAL(5,4) NULL,
    CONSTRAINT [UQ_EmailMessage_GraphMessageId] UNIQUE ([GraphMessageId])
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
    @ExcludePublicId UNIQUEIDENTIFIER = NULL
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
        SUM(CASE WHEN [AgentClassification] = 'vendor_newsletter'      THEN 1 ELSE 0 END) AS ClassVendorNewsletter,
        SUM(CASE WHEN [AgentClassification] = 'non_actionable'         THEN 1 ELSE 0 END) AS ClassNonActionable,
        SUM(CASE WHEN [AgentClassification] = 'unknown'                THEN 1 ELSE 0 END) AS ClassUnknown,
        SUM(CASE WHEN [AgentClassification] IS NULL                    THEN 1 ELSE 0 END) AS ClassUnclassified,

        -- Agent action counts
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_bill_specialist'        THEN 1 ELSE 0 END) AS ActionDelegatedBill,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_bill_credit_specialist' THEN 1 ELSE 0 END) AS ActionDelegatedBillCredit,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_expense_specialist'     THEN 1 ELSE 0 END) AS ActionDelegatedExpense,
        SUM(CASE WHEN [AgentDecidedAction] = 'flagged_needs_review'                THEN 1 ELSE 0 END) AS ActionFlaggedReview,
        SUM(CASE WHEN [AgentDecidedAction] = 'marked_irrelevant'                   THEN 1 ELSE 0 END) AS ActionMarkedIrrelevant,
        SUM(CASE WHEN [AgentDecidedAction] = 'marked_processed'                    THEN 1 ELSE 0 END) AS ActionMarkedProcessed,
        SUM(CASE WHEN [AgentDecidedAction] IS NULL                                  THEN 1 ELSE 0 END) AS ActionUnset,

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
END;
GO
GO
