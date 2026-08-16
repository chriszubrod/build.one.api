GO

IF OBJECT_ID('dbo.Attachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Attachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Filename] NVARCHAR(MAX) NOT NULL,
    [OriginalFilename] NVARCHAR(MAX) NOT NULL,
    [FileExtension] NVARCHAR(10) NULL,
    [ContentType] NVARCHAR(255) NOT NULL,
    [FileSize] BIGINT NOT NULL,
    [FileHash] NVARCHAR(64) NULL,
    [BlobUrl] NVARCHAR(MAX) NOT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Category] NVARCHAR(50) NULL,
    [Tags] NVARCHAR(MAX) NULL,
    [IsArchived] BIT NOT NULL DEFAULT 0,
    [Status] NVARCHAR(20) NULL,
    [DownloadCount] BIGINT NOT NULL DEFAULT 0,
    [LastDownloadedDatetime] DATETIME2(3) NULL,
    [ExpirationDate] DATETIME2(3) NULL,
    [StorageTier] NVARCHAR(20) NOT NULL DEFAULT 'Hot',
    -- U-187: sync-proof vendor invoice number parsed from the attachment's own
    -- extracted text. The QBO purchase->Expense connector never writes
    -- dbo.Attachment, so this column is immune to the KI-42 ReferenceNumber
    -- clobber. NULL until the extraction sweep populates it. (On prod the column
    -- is added by scripts/migrations/attachment_vendor_invoice_number.sql — apply
    -- that BEFORE re-applying this file, same layering as the extraction columns.)
    [VendorInvoiceNumber] NVARCHAR(100) NULL
);
END
GO


GO

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

CREATE OR ALTER PROCEDURE ReadAttachments
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByCategory
(
    @Category NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [Category] = @Category
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByHash
(
    @FileHash NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [FileHash] = @FileHash;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateAttachmentById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
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
    @IsArchived BIT,
    @Status NVARCHAR(20),
    @ExpirationDate DATETIME2(3),
    @StorageTier NVARCHAR(20)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Attachment]
    SET
        [ModifiedDatetime] = @Now,
        [Filename] = @Filename,
        [OriginalFilename] = @OriginalFilename,
        [FileExtension] = @FileExtension,
        [ContentType] = @ContentType,
        [FileSize] = @FileSize,
        [FileHash] = @FileHash,
        [BlobUrl] = @BlobUrl,
        [Description] = @Description,
        [Category] = @Category,
        [Tags] = @Tags,
        [IsArchived] = @IsArchived,
        [Status] = @Status,
        [ExpirationDate] = @ExpirationDate,
        [StorageTier] = @StorageTier
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
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Attachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Filename],
        DELETED.[OriginalFilename],
        DELETED.[FileExtension],
        DELETED.[ContentType],
        DELETED.[FileSize],
        DELETED.[FileHash],
        DELETED.[BlobUrl],
        DELETED.[Description],
        DELETED.[Category],
        DELETED.[Tags],
        DELETED.[IsArchived],
        DELETED.[Status],
        DELETED.[DownloadCount],
        CONVERT(VARCHAR(19), DELETED.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ExpirationDate], 120) AS [ExpirationDate],
        DELETED.[StorageTier]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentsByIds
(
    @Ids NVARCHAR(MAX)   -- comma-separated BIGINT IDs
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [Id] IN (
        SELECT CAST(LTRIM(RTRIM(value)) AS BIGINT)
        FROM STRING_SPLIT(@Ids, ',')
        WHERE LTRIM(RTRIM(value)) <> ''
    );

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE IncrementDownloadCount
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Attachment]
    SET
        [DownloadCount] = [DownloadCount] + 1,
        [LastDownloadedDatetime] = @Now
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
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetAttachmentQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50) = NULL,
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[Attachment]
        SET [QboId] = NULL, [RealmId] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[Attachment]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        @Stolen AS [Stolen]
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );
END;
GO
