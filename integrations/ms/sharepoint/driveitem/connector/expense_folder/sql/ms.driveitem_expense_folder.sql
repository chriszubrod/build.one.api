IF OBJECT_ID('ms.DriveItemExpenseFolder', 'U') IS NULL
BEGIN
CREATE TABLE [ms].[DriveItemExpenseFolder]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CompanyId] BIGINT NOT NULL,
    [MsDriveItemId] BIGINT NOT NULL,
    [FolderType] NVARCHAR(20) NOT NULL,
    CONSTRAINT [UQ_DriveItemExpenseFolder_CompanyId_FolderType] UNIQUE ([CompanyId], [FolderType]),
    CONSTRAINT [FK_DriveItemExpenseFolder_DriveItem] FOREIGN KEY ([MsDriveItemId]) REFERENCES [ms].[DriveItem]([Id]) ON DELETE CASCADE
);
END
GO



CREATE OR ALTER PROCEDURE CreateDriveItemExpenseFolder
(
    @CompanyId BIGINT,
    @MsDriveItemId BIGINT,
    @FolderType NVARCHAR(20)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[DriveItemExpenseFolder] ([CreatedDatetime], [ModifiedDatetime], [CompanyId], [MsDriveItemId], [FolderType])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CompanyId],
        INSERTED.[MsDriveItemId],
        INSERTED.[FolderType]
    VALUES (@Now, @Now, @CompanyId, @MsDriveItemId, @FolderType);

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadDriveItemExpenseFolderByCompanyIdAndFolderType
(
    @CompanyId BIGINT,
    @FolderType NVARCHAR(20)
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
        [CompanyId],
        [MsDriveItemId],
        [FolderType]
    FROM [ms].[DriveItemExpenseFolder]
    WHERE [CompanyId] = @CompanyId AND [FolderType] = @FolderType;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadDriveItemExpenseFolderByMsDriveItemId
(
    @MsDriveItemId BIGINT
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
        [CompanyId],
        [MsDriveItemId],
        [FolderType]
    FROM [ms].[DriveItemExpenseFolder]
    WHERE [MsDriveItemId] = @MsDriveItemId;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE DeleteDriveItemExpenseFolderByCompanyIdAndFolderType
(
    @CompanyId BIGINT,
    @FolderType NVARCHAR(20)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemExpenseFolder]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CompanyId],
        DELETED.[MsDriveItemId],
        DELETED.[FolderType]
    WHERE [CompanyId] = @CompanyId AND [FolderType] = @FolderType;

    COMMIT TRANSACTION;
END;
GO
