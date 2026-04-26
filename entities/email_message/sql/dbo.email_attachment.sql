-- EmailAttachment — child of EmailMessage. One row per file attached to
-- a polled email. Bytes are stored in Azure Blob Storage (same container
-- as the existing Attachment entity); BlobUri is the canonical reference.
--
-- Document Intelligence runs against BlobUri after persistence; the JSON
-- result lands in DiResultJson and the parsed/validated invoice fields
-- get hoisted to the strongly-typed columns (DiVendorName,
-- DiInvoiceNumber, DiTotalAmount, etc.) so downstream tools / reports
-- can query without re-parsing the JSON every time.
--
-- ExtractionStatus state machine:
--   pending          newly persisted, DI not yet run
--   extracted        DI succeeded, validation passed
--   validation_failed  DI succeeded but checksum/sanity failed
--   failed           DI returned a hard error
--   skipped          attachment is not a PDF/image (signature, footer img)

GO

IF OBJECT_ID('dbo.EmailAttachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[EmailAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [EmailMessageId] BIGINT NOT NULL,
    [GraphAttachmentId] NVARCHAR(255) NOT NULL,
    [Filename] NVARCHAR(512) NOT NULL,
    [ContentType] NVARCHAR(128) NULL,
    [SizeBytes] BIGINT NULL,
    [IsInline] BIT NOT NULL DEFAULT 0,
    [BlobUri] NVARCHAR(1024) NULL,
    [ExtractionStatus] NVARCHAR(50) NOT NULL DEFAULT 'pending',
    [ExtractedAt] DATETIME2(3) NULL,
    [DiModel] NVARCHAR(100) NULL,
    [DiResultJson] NVARCHAR(MAX) NULL,
    [DiConfidence] DECIMAL(5,4) NULL,
    [DiVendorName] NVARCHAR(255) NULL,
    [DiInvoiceNumber] NVARCHAR(100) NULL,
    [DiInvoiceDate] DATE NULL,
    [DiDueDate] DATE NULL,
    [DiSubtotal] DECIMAL(18,2) NULL,
    [DiTotalAmount] DECIMAL(18,2) NULL,
    [DiCurrency] NVARCHAR(10) NULL,
    [LastError] NVARCHAR(MAX) NULL,
    CONSTRAINT [FK_EmailAttachment_EmailMessage]
        FOREIGN KEY ([EmailMessageId]) REFERENCES [dbo].[EmailMessage]([Id])
);
END
GO

IF OBJECT_ID('dbo.EmailAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailAttachment_EmailMessageId' AND object_id = OBJECT_ID('dbo.EmailAttachment'))
BEGIN
CREATE INDEX IX_EmailAttachment_EmailMessageId ON [dbo].[EmailAttachment] ([EmailMessageId]);
END
GO

IF OBJECT_ID('dbo.EmailAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmailAttachment_ExtractionStatus' AND object_id = OBJECT_ID('dbo.EmailAttachment'))
BEGIN
CREATE INDEX IX_EmailAttachment_ExtractionStatus ON [dbo].[EmailAttachment] ([ExtractionStatus]);
END
GO

IF OBJECT_ID('dbo.EmailAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_EmailAttachment_Email_Graph' AND object_id = OBJECT_ID('dbo.EmailAttachment'))
BEGIN
CREATE UNIQUE INDEX UQ_EmailAttachment_Email_Graph ON [dbo].[EmailAttachment] ([EmailMessageId], [GraphAttachmentId]);
END
GO

GO

CREATE OR ALTER PROCEDURE UpsertEmailAttachment
(
    @EmailMessageId BIGINT,
    @GraphAttachmentId NVARCHAR(255),
    @Filename NVARCHAR(512),
    @ContentType NVARCHAR(128) = NULL,
    @SizeBytes BIGINT = NULL,
    @IsInline BIT = 0,
    @BlobUri NVARCHAR(1024) = NULL
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
             [ExtractionStatus])
        VALUES
            (@Now, @Now, @EmailMessageId, @GraphAttachmentId,
             @Filename, @ContentType, @SizeBytes, @IsInline, @BlobUri,
             'pending')
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

GO

CREATE OR ALTER PROCEDURE ReadEmailAttachmentsByEmailMessageId
(
    @EmailMessageId BIGINT
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
        [EmailMessageId],
        [GraphAttachmentId],
        [Filename],
        [ContentType],
        [SizeBytes],
        [IsInline],
        [BlobUri],
        [ExtractionStatus],
        CONVERT(VARCHAR(19), [ExtractedAt], 120) AS [ExtractedAt],
        [DiModel],
        [DiResultJson],
        [DiConfidence],
        [DiVendorName],
        [DiInvoiceNumber],
        CONVERT(VARCHAR(10), [DiInvoiceDate], 120) AS [DiInvoiceDate],
        CONVERT(VARCHAR(10), [DiDueDate], 120) AS [DiDueDate],
        [DiSubtotal],
        [DiTotalAmount],
        [DiCurrency],
        [LastError]
    FROM dbo.[EmailAttachment]
    WHERE [EmailMessageId] = @EmailMessageId
    ORDER BY [CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadEmailAttachmentByPublicId
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
        [EmailMessageId],
        [GraphAttachmentId],
        [Filename],
        [ContentType],
        [SizeBytes],
        [IsInline],
        [BlobUri],
        [ExtractionStatus],
        CONVERT(VARCHAR(19), [ExtractedAt], 120) AS [ExtractedAt],
        [DiModel],
        [DiResultJson],
        [DiConfidence],
        [DiVendorName],
        [DiInvoiceNumber],
        CONVERT(VARCHAR(10), [DiInvoiceDate], 120) AS [DiInvoiceDate],
        CONVERT(VARCHAR(10), [DiDueDate], 120) AS [DiDueDate],
        [DiSubtotal],
        [DiTotalAmount],
        [DiCurrency],
        [LastError]
    FROM dbo.[EmailAttachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateEmailAttachmentExtraction
(
    @Id BIGINT,
    @ExtractionStatus NVARCHAR(50),
    @DiModel NVARCHAR(100) = NULL,
    @DiResultJson NVARCHAR(MAX) = NULL,
    @DiConfidence DECIMAL(5,4) = NULL,
    @DiVendorName NVARCHAR(255) = NULL,
    @DiInvoiceNumber NVARCHAR(100) = NULL,
    @DiInvoiceDate DATE = NULL,
    @DiDueDate DATE = NULL,
    @DiSubtotal DECIMAL(18,2) = NULL,
    @DiTotalAmount DECIMAL(18,2) = NULL,
    @DiCurrency NVARCHAR(10) = NULL,
    @LastError NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[EmailAttachment]
    SET
        [ModifiedDatetime] = @Now,
        [ExtractionStatus] = @ExtractionStatus,
        [ExtractedAt] = @Now,
        [DiModel] = @DiModel,
        [DiResultJson] = @DiResultJson,
        [DiConfidence] = @DiConfidence,
        [DiVendorName] = @DiVendorName,
        [DiInvoiceNumber] = @DiInvoiceNumber,
        [DiInvoiceDate] = @DiInvoiceDate,
        [DiDueDate] = @DiDueDate,
        [DiSubtotal] = @DiSubtotal,
        [DiTotalAmount] = @DiTotalAmount,
        [DiCurrency] = @DiCurrency,
        [LastError] = @LastError
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[ExtractionStatus],
        INSERTED.[DiVendorName],
        INSERTED.[DiInvoiceNumber],
        INSERTED.[DiTotalAmount],
        INSERTED.[LastError]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
