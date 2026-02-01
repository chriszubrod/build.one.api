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
    [StorageTier] NVARCHAR(20) NOT NULL DEFAULT 'Hot'
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



GO

CREATE OR ALTER PROCEDURE ReadAttachments
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



GO

CREATE OR ALTER PROCEDURE ReadAttachmentById
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



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByCategory
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



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByHash
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



SELECT COUNT(Id) AS TotalCount
FROM dbo.Attachment;
