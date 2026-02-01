IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NULL
BEGIN
CREATE TABLE ms.DriveItem
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [MsDriveId] BIGINT NOT NULL,
    [ItemId] NVARCHAR(255) NOT NULL,
    [ParentItemId] NVARCHAR(255) NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [ItemType] NVARCHAR(50) NOT NULL,
    [Size] BIGINT NULL,
    [MimeType] NVARCHAR(255) NULL,
    [WebUrl] NVARCHAR(MAX) NOT NULL,
    [GraphCreatedDatetime] DATETIME2(3) NULL,
    [GraphModifiedDatetime] DATETIME2(3) NULL,
    CONSTRAINT FK_DriveItem_Drive FOREIGN KEY ([MsDriveId]) REFERENCES ms.Drive([Id]) ON DELETE CASCADE
);
END
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DriveItem_ItemId' AND object_id = OBJECT_ID('ms.DriveItem'))
BEGIN
CREATE UNIQUE INDEX IX_DriveItem_ItemId ON ms.DriveItem ([ItemId]);
END
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DriveItem_MsDriveId' AND object_id = OBJECT_ID('ms.DriveItem'))
BEGIN
CREATE INDEX IX_DriveItem_MsDriveId ON ms.DriveItem ([MsDriveId]);
END
GO

IF OBJECT_ID('ms.DriveItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DriveItem_ParentItemId' AND object_id = OBJECT_ID('ms.DriveItem'))
BEGIN
CREATE INDEX IX_DriveItem_ParentItemId ON ms.DriveItem ([ParentItemId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateMsDriveItem
(
    @MsDriveId BIGINT,
    @ItemId NVARCHAR(255),
    @ParentItemId NVARCHAR(255),
    @Name NVARCHAR(255),
    @ItemType NVARCHAR(50),
    @Size BIGINT,
    @MimeType NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @GraphCreatedDatetime DATETIME2(3),
    @GraphModifiedDatetime DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO ms.DriveItem ([CreatedDatetime], [ModifiedDatetime], [MsDriveId], [ItemId], [ParentItemId], [Name], [ItemType], [Size], [MimeType], [WebUrl], [GraphCreatedDatetime], [GraphModifiedDatetime])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[MsDriveId],
        INSERTED.[ItemId],
        INSERTED.[ParentItemId],
        INSERTED.[Name],
        INSERTED.[ItemType],
        INSERTED.[Size],
        INSERTED.[MimeType],
        INSERTED.[WebUrl],
        CONVERT(VARCHAR(19), INSERTED.[GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    VALUES (@Now, @Now, @MsDriveId, @ItemId, @ParentItemId, @Name, @ItemType, @Size, @MimeType, @WebUrl, @GraphCreatedDatetime, @GraphModifiedDatetime);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItems
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
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemsByMsDriveId
(
    @MsDriveId BIGINT
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
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [MsDriveId] = @MsDriveId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemByPublicId
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
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemByItemId
(
    @ItemId NVARCHAR(255)
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
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [ItemId] = @ItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadMsDriveItemsByParentItemId
(
    @ParentItemId NVARCHAR(255)
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
        [MsDriveId],
        [ItemId],
        [ParentItemId],
        [Name],
        [ItemType],
        [Size],
        [MimeType],
        [WebUrl],
        CONVERT(VARCHAR(19), [GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), [GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    FROM ms.DriveItem
    WHERE [ParentItemId] = @ParentItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateMsDriveItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @MsDriveId BIGINT,
    @ItemId NVARCHAR(255),
    @ParentItemId NVARCHAR(255),
    @Name NVARCHAR(255),
    @ItemType NVARCHAR(50),
    @Size BIGINT,
    @MimeType NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @GraphCreatedDatetime DATETIME2(3),
    @GraphModifiedDatetime DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE ms.DriveItem
    SET [ModifiedDatetime] = @Now,
        [MsDriveId] = @MsDriveId,
        [ItemId] = @ItemId,
        [ParentItemId] = @ParentItemId,
        [Name] = @Name,
        [ItemType] = @ItemType,
        [Size] = @Size,
        [MimeType] = @MimeType,
        [WebUrl] = @WebUrl,
        [GraphCreatedDatetime] = @GraphCreatedDatetime,
        [GraphModifiedDatetime] = @GraphModifiedDatetime
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[MsDriveId],
        INSERTED.[ItemId],
        INSERTED.[ParentItemId],
        INSERTED.[Name],
        INSERTED.[ItemType],
        INSERTED.[Size],
        INSERTED.[MimeType],
        INSERTED.[WebUrl],
        CONVERT(VARCHAR(19), INSERTED.[GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteMsDriveItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM ms.DriveItem
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[MsDriveId],
        DELETED.[ItemId],
        DELETED.[ParentItemId],
        DELETED.[Name],
        DELETED.[ItemType],
        DELETED.[Size],
        DELETED.[MimeType],
        DELETED.[WebUrl],
        CONVERT(VARCHAR(19), DELETED.[GraphCreatedDatetime], 120) AS [GraphCreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[GraphModifiedDatetime], 120) AS [GraphModifiedDatetime]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


    @PublicId = '0143726e-1155-47b4-9750-f5ea4362a605';


