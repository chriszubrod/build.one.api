DROP TABLE IF EXISTS [dbo].[Attachment];
GO

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
    [StorageTier] NVARCHAR(20) NOT NULL DEFAULT 'Hot'
);
GO


DROP PROCEDURE IF EXISTS CreateAttachment;
GO

CREATE PROCEDURE CreateAttachment
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
    @StorageTier NVARCHAR(20) = 'Hot'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Attachment] ([CreatedDatetime], [ModifiedDatetime], [Filename], [OriginalFilename], [FileExtension], [ContentType], [FileSize], [FileHash], [BlobUrl], [Description], [Category], [Tags], [IsArchived], [Status], [ExpirationDate], [StorageTier])
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
    VALUES (@Now, @Now, @Filename, @OriginalFilename, @FileExtension, @ContentType, @FileSize, @FileHash, @BlobUrl, @Description, @Category, @Tags, @IsArchived, @Status, @ExpirationDate, @StorageTier);

    COMMIT TRANSACTION;
END;

EXEC CreateAttachment
    @Filename = 'test.pdf',
    @OriginalFilename = 'test.pdf',
    @FileExtension = 'pdf',
    @ContentType = 'application/pdf',
    @FileSize = 1024,
    @FileHash = NULL,
    @BlobUrl = 'https://example.blob.core.windows.net/attachments/test.pdf',
    @Description = 'Test attachment',
    @Category = 'Invoice',
    @Tags = NULL,
    @IsArchived = 0,
    @Status = 'Draft',
    @ExpirationDate = NULL,
    @StorageTier = 'Hot';
GO


DROP PROCEDURE IF EXISTS ReadAttachments;
GO

CREATE PROCEDURE ReadAttachments
AS
BEGIN
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
        [StorageTier]
    FROM dbo.[Attachment]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;

EXEC ReadAttachments;
GO


DROP PROCEDURE IF EXISTS ReadAttachmentById;
GO

CREATE PROCEDURE ReadAttachmentById
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
        [StorageTier]
    FROM dbo.[Attachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadAttachmentById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadAttachmentByPublicId;
GO

CREATE PROCEDURE ReadAttachmentByPublicId
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
        [StorageTier]
    FROM dbo.[Attachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadAttachmentByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadAttachmentByCategory;
GO

CREATE PROCEDURE ReadAttachmentByCategory
(
    @Category NVARCHAR(50)
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
        [StorageTier]
    FROM dbo.[Attachment]
    WHERE [Category] = @Category
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;

EXEC ReadAttachmentByCategory
    @Category = 'Invoice';
GO


DROP PROCEDURE IF EXISTS ReadAttachmentByHash;
GO

CREATE PROCEDURE ReadAttachmentByHash
(
    @FileHash NVARCHAR(64)
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
        [StorageTier]
    FROM dbo.[Attachment]
    WHERE [FileHash] = @FileHash;

    COMMIT TRANSACTION;
END;

EXEC ReadAttachmentByHash
    @FileHash = 'abc123';
GO


DROP PROCEDURE IF EXISTS UpdateAttachmentById;
GO

CREATE PROCEDURE UpdateAttachmentById
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

EXEC UpdateAttachmentById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @Filename = 'test_updated.pdf',
    @OriginalFilename = 'test.pdf',
    @FileExtension = 'pdf',
    @ContentType = 'application/pdf',
    @FileSize = 2048,
    @FileHash = NULL,
    @BlobUrl = 'https://example.blob.core.windows.net/attachments/test.pdf',
    @Description = 'Updated test attachment',
    @Category = 'Invoice',
    @Tags = NULL,
    @IsArchived = 0,
    @Status = 'Approved',
    @ExpirationDate = NULL,
    @StorageTier = 'Hot';
GO


DROP PROCEDURE IF EXISTS DeleteAttachmentById;
GO

CREATE PROCEDURE DeleteAttachmentById
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

EXEC DeleteAttachmentById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS IncrementDownloadCount;
GO

CREATE PROCEDURE IncrementDownloadCount
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

EXEC IncrementDownloadCount
    @Id = 1;
GO


SELECT COUNT(Id) AS TotalCount
FROM dbo.Attachment;