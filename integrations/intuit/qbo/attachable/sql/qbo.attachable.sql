-- QBO Attachable Schema
-- Stores QBO Attachable records locally

IF OBJECT_ID('qbo.Attachable', 'U') IS NULL
BEGIN
    CREATE TABLE [qbo].[Attachable]
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [QboId] NVARCHAR(50) NOT NULL,
        [SyncToken] NVARCHAR(50) NULL,
        [RealmId] NVARCHAR(50) NOT NULL,
        [FileName] NVARCHAR(500) NULL,
        [Note] NVARCHAR(4000) NULL,
        [Category] NVARCHAR(100) NULL,
        [ContentType] NVARCHAR(200) NULL,
        [Size] BIGINT NULL,
        [FileAccessUri] NVARCHAR(2000) NULL,
        [TempDownloadUri] NVARCHAR(2000) NULL,
        [EntityRefType] NVARCHAR(50) NULL,
        [EntityRefValue] NVARCHAR(50) NULL
    );
END;
GO

IF OBJECT_ID('qbo.Attachable', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_QboAttachable_QboId_RealmId' AND object_id = OBJECT_ID('qbo.Attachable'))
BEGIN
CREATE UNIQUE INDEX UX_QboAttachable_QboId_RealmId ON [qbo].[Attachable] ([QboId], [RealmId]);
END
GO

IF OBJECT_ID('qbo.Attachable', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboAttachable_RealmId' AND object_id = OBJECT_ID('qbo.Attachable'))
BEGIN
CREATE INDEX IX_QboAttachable_RealmId ON [qbo].[Attachable] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Attachable', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboAttachable_EntityRef' AND object_id = OBJECT_ID('qbo.Attachable'))
BEGIN
CREATE INDEX IX_QboAttachable_EntityRef ON [qbo].[Attachable] ([EntityRefType], [EntityRefValue]);
END
GO


-- Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboAttachable
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @FileName NVARCHAR(500),
    @Note NVARCHAR(4000),
    @Category NVARCHAR(100),
    @ContentType NVARCHAR(200),
    @Size BIGINT,
    @FileAccessUri NVARCHAR(2000),
    @TempDownloadUri NVARCHAR(2000),
    @EntityRefType NVARCHAR(50),
    @EntityRefValue NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Attachable] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [FileName], [Note], [Category], [ContentType], [Size],
        [FileAccessUri], [TempDownloadUri], [EntityRefType], [EntityRefValue]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[FileName],
        INSERTED.[Note],
        INSERTED.[Category],
        INSERTED.[ContentType],
        INSERTED.[Size],
        INSERTED.[FileAccessUri],
        INSERTED.[TempDownloadUri],
        INSERTED.[EntityRefType],
        INSERTED.[EntityRefValue]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @FileName, @Note, @Category, @ContentType, @Size,
        @FileAccessUri, @TempDownloadUri, @EntityRefType, @EntityRefValue
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboAttachableById
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
        [QboId],
        [SyncToken],
        [RealmId],
        [FileName],
        [Note],
        [Category],
        [ContentType],
        [Size],
        [FileAccessUri],
        [TempDownloadUri],
        [EntityRefType],
        [EntityRefValue]
    FROM [qbo].[Attachable]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboAttachableByQboId
(
    @QboId NVARCHAR(50)
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
        [QboId],
        [SyncToken],
        [RealmId],
        [FileName],
        [Note],
        [Category],
        [ContentType],
        [Size],
        [FileAccessUri],
        [TempDownloadUri],
        [EntityRefType],
        [EntityRefValue]
    FROM [qbo].[Attachable]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboAttachableByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
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
        [QboId],
        [SyncToken],
        [RealmId],
        [FileName],
        [Note],
        [Category],
        [ContentType],
        [Size],
        [FileAccessUri],
        [TempDownloadUri],
        [EntityRefType],
        [EntityRefValue]
    FROM [qbo].[Attachable]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboAttachablesByEntityRef
(
    @EntityRefType NVARCHAR(50),
    @EntityRefValue NVARCHAR(50),
    @RealmId NVARCHAR(50)
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
        [QboId],
        [SyncToken],
        [RealmId],
        [FileName],
        [Note],
        [Category],
        [ContentType],
        [Size],
        [FileAccessUri],
        [TempDownloadUri],
        [EntityRefType],
        [EntityRefValue]
    FROM [qbo].[Attachable]
    WHERE [EntityRefType] = @EntityRefType 
      AND [EntityRefValue] = @EntityRefValue
      AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboAttachablesByRealmId
(
    @RealmId NVARCHAR(50)
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
        [QboId],
        [SyncToken],
        [RealmId],
        [FileName],
        [Note],
        [Category],
        [ContentType],
        [Size],
        [FileAccessUri],
        [TempDownloadUri],
        [EntityRefType],
        [EntityRefValue]
    FROM [qbo].[Attachable]
    WHERE [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboAttachableByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @FileName NVARCHAR(500),
    @Note NVARCHAR(4000),
    @Category NVARCHAR(100),
    @ContentType NVARCHAR(200),
    @Size BIGINT,
    @FileAccessUri NVARCHAR(2000),
    @TempDownloadUri NVARCHAR(2000),
    @EntityRefType NVARCHAR(50),
    @EntityRefValue NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Attachable]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = @SyncToken,
        [FileName] = @FileName,
        [Note] = @Note,
        [Category] = @Category,
        [ContentType] = @ContentType,
        [Size] = @Size,
        [FileAccessUri] = @FileAccessUri,
        [TempDownloadUri] = @TempDownloadUri,
        [EntityRefType] = @EntityRefType,
        [EntityRefValue] = @EntityRefValue
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[FileName],
        INSERTED.[Note],
        INSERTED.[Category],
        INSERTED.[ContentType],
        INSERTED.[Size],
        INSERTED.[FileAccessUri],
        INSERTED.[TempDownloadUri],
        INSERTED.[EntityRefType],
        INSERTED.[EntityRefValue]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboAttachableByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Attachable]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[FileName],
        DELETED.[Note],
        DELETED.[Category],
        DELETED.[ContentType],
        DELETED.[Size],
        DELETED.[FileAccessUri],
        DELETED.[TempDownloadUri],
        DELETED.[EntityRefType],
        DELETED.[EntityRefValue]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


SELECT COUNT(Id) AS TotalCount
FROM qbo.Attachable;
