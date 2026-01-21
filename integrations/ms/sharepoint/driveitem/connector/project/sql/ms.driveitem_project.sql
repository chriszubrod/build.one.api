DROP TABLE IF EXISTS [ms].[DriveItemProject];
GO

CREATE TABLE [ms].[DriveItemProject]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NOT NULL,
    [MsDriveItemId] BIGINT NOT NULL,
    CONSTRAINT [UQ_DriveItemProject_ProjectId] UNIQUE ([ProjectId]),
    CONSTRAINT [UQ_DriveItemProject_MsDriveItemId] UNIQUE ([MsDriveItemId]),
    CONSTRAINT [FK_DriveItemProject_DriveItem] FOREIGN KEY ([MsDriveItemId]) REFERENCES [ms].[DriveItem]([Id]) ON DELETE CASCADE
);
GO


DROP PROCEDURE IF EXISTS CreateDriveItemProject;
GO

CREATE PROCEDURE CreateDriveItemProject
(
    @ProjectId BIGINT,
    @MsDriveItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[DriveItemProject] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [MsDriveItemId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[MsDriveItemId]
    VALUES (@Now, @Now, @ProjectId, @MsDriveItemId);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveItemProjectById;
GO

CREATE PROCEDURE ReadDriveItemProjectById
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
        [MsDriveItemId]
    FROM [ms].[DriveItemProject]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveItemProjectByProjectId;
GO

CREATE PROCEDURE ReadDriveItemProjectByProjectId
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
        [MsDriveItemId]
    FROM [ms].[DriveItemProject]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveItemProjectByMsDriveItemId;
GO

CREATE PROCEDURE ReadDriveItemProjectByMsDriveItemId
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
        [MsDriveItemId]
    FROM [ms].[DriveItemProject]
    WHERE [MsDriveItemId] = @MsDriveItemId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteDriveItemProjectById;
GO

CREATE PROCEDURE DeleteDriveItemProjectById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemProject]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[MsDriveItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteDriveItemProjectByProjectId;
GO

CREATE PROCEDURE DeleteDriveItemProjectByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemProject]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[MsDriveItemId]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO

SELECT * FROM [ms].[DriveItemProject];
