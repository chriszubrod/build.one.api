DROP TABLE IF EXISTS [ms].[DriveItemProjectModule];
GO

CREATE TABLE [ms].[DriveItemProjectModule]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NOT NULL,
    [ModuleId] BIGINT NOT NULL,
    [MsDriveItemId] BIGINT NOT NULL,
    CONSTRAINT [UQ_DriveItemProjectModule_ProjectId_ModuleId] UNIQUE ([ProjectId], [ModuleId]),
    CONSTRAINT [UQ_DriveItemProjectModule_MsDriveItemId] UNIQUE ([MsDriveItemId]),
    CONSTRAINT [FK_DriveItemProjectModule_DriveItem] FOREIGN KEY ([MsDriveItemId]) REFERENCES [ms].[DriveItem]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_DriveItemProjectModule_Module] FOREIGN KEY ([ModuleId]) REFERENCES [dbo].[Module]([Id]) ON DELETE CASCADE
);
GO


DROP PROCEDURE IF EXISTS CreateDriveItemProjectModule;
GO

CREATE PROCEDURE CreateDriveItemProjectModule
(
    @ProjectId BIGINT,
    @ModuleId BIGINT,
    @MsDriveItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[DriveItemProjectModule] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [ModuleId], [MsDriveItemId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[ModuleId],
        INSERTED.[MsDriveItemId]
    VALUES (@Now, @Now, @ProjectId, @ModuleId, @MsDriveItemId);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveItemProjectModuleById;
GO

CREATE PROCEDURE ReadDriveItemProjectModuleById
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
        [ModuleId],
        [MsDriveItemId]
    FROM [ms].[DriveItemProjectModule]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveItemProjectModuleByProjectIdAndModuleId;
GO

CREATE PROCEDURE ReadDriveItemProjectModuleByProjectIdAndModuleId
(
    @ProjectId BIGINT,
    @ModuleId BIGINT
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
        [ModuleId],
        [MsDriveItemId]
    FROM [ms].[DriveItemProjectModule]
    WHERE [ProjectId] = @ProjectId AND [ModuleId] = @ModuleId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveItemProjectModulesByProjectId;
GO

CREATE PROCEDURE ReadDriveItemProjectModulesByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        dipm.[Id],
        dipm.[PublicId],
        dipm.[RowVersion],
        CONVERT(VARCHAR(19), dipm.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), dipm.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        dipm.[ProjectId],
        dipm.[ModuleId],
        dipm.[MsDriveItemId]
    FROM [ms].[DriveItemProjectModule] dipm
    WHERE dipm.[ProjectId] = @ProjectId
    ORDER BY dipm.[ModuleId] ASC;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadDriveItemProjectModuleByMsDriveItemId;
GO

CREATE PROCEDURE ReadDriveItemProjectModuleByMsDriveItemId
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
        [ModuleId],
        [MsDriveItemId]
    FROM [ms].[DriveItemProjectModule]
    WHERE [MsDriveItemId] = @MsDriveItemId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteDriveItemProjectModuleById;
GO

CREATE PROCEDURE DeleteDriveItemProjectModuleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemProjectModule]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[ModuleId],
        DELETED.[MsDriveItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteDriveItemProjectModuleByProjectIdAndModuleId;
GO

CREATE PROCEDURE DeleteDriveItemProjectModuleByProjectIdAndModuleId
(
    @ProjectId BIGINT,
    @ModuleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[DriveItemProjectModule]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[ModuleId],
        DELETED.[MsDriveItemId]
    WHERE [ProjectId] = @ProjectId AND [ModuleId] = @ModuleId;

    COMMIT TRANSACTION;
END;
GO

SELECT * FROM [ms].[DriveItemProjectModule];
