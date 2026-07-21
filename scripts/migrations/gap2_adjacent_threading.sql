-- =====================================================================
-- Gap 2 Phase Adjacent — thread CreatedByUserId on 10 attachment /
-- email / review / bill-folder Create + Upsert sprocs.
--
-- Pattern: add @CreatedByUserId BIGINT = NULL param; INSERT uses
-- COALESCE(@CreatedByUserId, 17) preserving the DEFAULT-trick fallback
-- for scheduler / system context.
--
-- Skipped:
--   ReviewEntry — table exists (Phase 5 + Gap 2 added columns) but no
--   Create sproc and no service code references it; the entity was
--   decommissioned alongside email-intake. Carries DEFAULT (17) until
--   the table is dropped.
--
-- Special:
--   EmailMessage / EmailAttachment use UPSERT (MERGE) sprocs, not
--   CREATE. The UPDATE branch deliberately does NOT touch
--   CreatedByUserId — once stamped, it stays with the original creator.
--   Only the INSERT branch threads the new param.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ===== 1. CreateAttachment =====
CREATE OR ALTER PROCEDURE CreateAttachment
(
    @Filename NVARCHAR(MAX),
    @OriginalFilename NVARCHAR(MAX),
    @FileExtension NVARCHAR(10),
    @ContentType NVARCHAR(255),
    @FileSize BIGINT,
    @FileHash NVARCHAR(64),
    @BlobUrl NVARCHAR(MAX),
    @Description NVARCHAR(MAX),
    @Category NVARCHAR(50),
    @Tags NVARCHAR(MAX),
    @IsArchived BIT = 0,
    @Status NVARCHAR(20),
    @ExpirationDate DATETIME2(3),
    @StorageTier NVARCHAR(20) = 'Hot',
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Attachment] ([CreatedDatetime], [ModifiedDatetime], [Filename], [OriginalFilename], [FileExtension], [ContentType], [FileSize], [FileHash], [BlobUrl], [Description], [Category], [Tags], [IsArchived], [Status], [ExpirationDate], [StorageTier], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Filename],
        INSERTED.[OriginalFilename],
        INSERTED.[FileExtension],
        INSERTED.[ContentType],
        INSERTED.[FileSize],
        INSERTED.[FileHash],
        INSERTED.[BlobUrl],
        INSERTED.[Description],
        INSERTED.[Category],
        INSERTED.[Tags],
        INSERTED.[IsArchived],
        INSERTED.[Status],
        INSERTED.[DownloadCount],
        CONVERT(VARCHAR(19), INSERTED.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ExpirationDate], 120) AS [ExpirationDate],
        INSERTED.[StorageTier]
    VALUES (@Now, @Now, @Filename, @OriginalFilename, @FileExtension, @ContentType, @FileSize, @FileHash, @BlobUrl, @Description, @Category, @Tags, @IsArchived, @Status, @ExpirationDate, @StorageTier, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 2. CreateBillLineItemAttachment =====
CREATE OR ALTER PROCEDURE CreateBillLineItemAttachment
(
    @BillLineItemId BIGINT,
    @AttachmentId BIGINT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [BillLineItemId], [AttachmentId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillLineItemId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @BillLineItemId, @AttachmentId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 3. CreateExpenseLineItemAttachment =====
CREATE OR ALTER PROCEDURE CreateExpenseLineItemAttachment
(
    @ExpenseLineItemId BIGINT,
    @AttachmentId BIGINT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ExpenseLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [ExpenseLineItemId], [AttachmentId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ExpenseLineItemId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @ExpenseLineItemId, @AttachmentId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 4. CreateInvoiceLineItemAttachment =====
CREATE OR ALTER PROCEDURE CreateInvoiceLineItemAttachment
(
    @InvoiceLineItemId BIGINT,
    @AttachmentId BIGINT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[InvoiceLineItemAttachment] ([CreatedDatetime], [ModifiedDatetime], [InvoiceLineItemId], [AttachmentId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceLineItemId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @InvoiceLineItemId, @AttachmentId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 5. UpsertEmailMessage (canonical = recipients version) =====
-- INSERT branch threads CreatedByUserId; UPDATE branch leaves it alone.
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
    @CreatedByUserId BIGINT = NULL
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
             [ProcessingStatus], [WebLink], [HasAttachments], [CreatedByUserId])
        VALUES
            (@Now, @Now, @GraphMessageId, @InternetMessageId,
             @ConversationId, @MailboxAddress, @FromAddress, @FromName,
             @ToRecipients, @CcRecipients,
             @Subject, @BodyPreview, @BodyContent, @BodyContentType, @ReceivedDatetime,
             'pending', @WebLink, @HasAttachments, COALESCE(@CreatedByUserId, 17))
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

-- ===== 6. UpsertEmailAttachment =====
CREATE OR ALTER PROCEDURE UpsertEmailAttachment
(
    @EmailMessageId BIGINT,
    @GraphAttachmentId NVARCHAR(255),
    @Filename NVARCHAR(512),
    @ContentType NVARCHAR(128) = NULL,
    @SizeBytes BIGINT = NULL,
    @IsInline BIT = 0,
    @BlobUri NVARCHAR(1024) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[EmailAttachment] AS target
    USING (SELECT @EmailMessageId AS EmailMessageId, @GraphAttachmentId AS GraphAttachmentId) AS source
    ON target.[EmailMessageId] = source.EmailMessageId
       AND target.[GraphAttachmentId] = source.GraphAttachmentId
    WHEN MATCHED THEN
        UPDATE SET
            [ModifiedDatetime] = @Now,
            [Filename] = @Filename,
            [ContentType] = @ContentType,
            [SizeBytes] = @SizeBytes,
            [IsInline] = @IsInline,
            [BlobUri] = CASE WHEN @BlobUri IS NULL THEN [BlobUri] ELSE @BlobUri END
    WHEN NOT MATCHED THEN
        INSERT
            ([CreatedDatetime], [ModifiedDatetime], [EmailMessageId], [GraphAttachmentId],
             [Filename], [ContentType], [SizeBytes], [IsInline], [BlobUri],
             [ExtractionStatus], [CreatedByUserId])
        VALUES
            (@Now, @Now, @EmailMessageId, @GraphAttachmentId,
             @Filename, @ContentType, @SizeBytes, @IsInline, @BlobUri,
             'pending', COALESCE(@CreatedByUserId, 17))
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmailMessageId],
        INSERTED.[GraphAttachmentId],
        INSERTED.[Filename],
        INSERTED.[ContentType],
        INSERTED.[SizeBytes],
        INSERTED.[IsInline],
        INSERTED.[BlobUri],
        INSERTED.[ExtractionStatus];

    COMMIT TRANSACTION;
END;
GO

-- ===== 7. CreateReview =====
CREATE OR ALTER PROCEDURE CreateReview
(
    @ReviewStatusId BIGINT,
    @UserId         BIGINT,
    @Comments       NVARCHAR(MAX) = NULL,
    @BillId         BIGINT = NULL,
    @ExpenseId      BIGINT = NULL,
    @BillCreditId   BIGINT = NULL,
    @InvoiceId      BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Review] (
        [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId],
        [CreatedByUserId]
    )
    VALUES (
        @Now, @Now,
        @ReviewStatusId, @UserId, @Comments,
        @BillId, @ExpenseId, @BillCreditId, @InvoiceId,
        COALESCE(@CreatedByUserId, 17)
    );

    SELECT * FROM dbo.[vw_Review] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO

-- ===== 8. CreateReviewStatus =====
CREATE OR ALTER PROCEDURE CreateReviewStatus
(
    @Name NVARCHAR(100),
    @Description NVARCHAR(500) = NULL,
    @SortOrder INT = 0,
    @IsFinal BIT = 0,
    @IsDeclined BIT = 0,
    @IsActive BIT = 1,
    @Color NVARCHAR(7) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[SortOrder],
        INSERTED.[IsFinal],
        INSERTED.[IsDeclined],
        INSERTED.[IsActive],
        INSERTED.[Color]
    VALUES (@Now, @Now, @Name, @Description, @SortOrder, @IsFinal, @IsDeclined, @IsActive, @Color, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 9. CreateBillFolderRun =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill/sql/dbo.billfolderrun.sql
--
-- Re-running this file is now a no-op for CreateBillFolderRun. Do NOT reintroduce a
-- body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 10. CreateBillFolderRunItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill/sql/dbo.billfolderrunitem.sql
--
-- Re-running this file is now a no-op for CreateBillFolderRunItem. Do NOT reintroduce a
-- body here.
-- ---------------------------------------------------------------------------
GO

PRINT 'Gap 2 Phase Adjacent: 10 sprocs threaded with @CreatedByUserId (ReviewEntry skipped — decommissioned)';
GO
