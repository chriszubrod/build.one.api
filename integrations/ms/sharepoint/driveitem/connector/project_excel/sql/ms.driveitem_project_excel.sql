GO

IF OBJECT_ID('ms.DriveItemProjectExcel', 'U') IS NULL
BEGIN
CREATE TABLE [ms].[DriveItemProjectExcel]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NOT NULL,
    [MsDriveItemId] BIGINT NOT NULL,
    [WorksheetName] NVARCHAR(255) NOT NULL,
    CONSTRAINT [UQ_DriveItemProjectExcel_ProjectId] UNIQUE ([ProjectId]),
    CONSTRAINT [UQ_DriveItemProjectExcel_MsDriveItemId] UNIQUE ([MsDriveItemId]),
    CONSTRAINT [FK_DriveItemProjectExcel_DriveItem] FOREIGN KEY ([MsDriveItemId]) REFERENCES [ms].[DriveItem]([Id]) ON DELETE CASCADE
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateDriveItemProjectExcel
(
    @ProjectId BIGINT,
    @MsDriveItemId BIGINT,
    @WorksheetName NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[DriveItemProjectExcel] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [MsDriveItemId], [WorksheetName])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[MsDriveItemId],
        INSERTED.[WorksheetName]
    VALUES (@Now, @Now, @ProjectId, @MsDriveItemId, @WorksheetName);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadDriveItemProjectExcelById
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
        [ProjectId],
        [MsDriveItemId],
        [WorksheetName]
    FROM [ms].[DriveItemProjectExcel]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadDriveItemProjectExcelByProjectId
(
    @ProjectId BIGINT
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
        [ProjectId],
        [MsDriveItemId],
        [WorksheetName]
    FROM [ms].[DriveItemProjectExcel]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadDriveItemProjectExcelByMsDriveItemId
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
        [ProjectId],
        [MsDriveItemId],
        [WorksheetName]
    FROM [ms].[DriveItemProjectExcel]
    WHERE [MsDriveItemId] = @MsDriveItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteDriveItemProjectExcelById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemProjectExcel]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[MsDriveItemId],
        DELETED.[WorksheetName]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteDriveItemProjectExcelByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemProjectExcel]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[MsDriveItemId],
        DELETED.[WorksheetName]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO

