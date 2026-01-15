IF OBJECT_ID('ms.Drive', 'U') IS NOT NULL
    DROP TABLE ms.Drive;
GO

CREATE TABLE ms.Drive
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [MsSiteId] BIGINT NOT NULL,
    [DriveId] NVARCHAR(255) NOT NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [WebUrl] NVARCHAR(MAX) NOT NULL,
    [DriveType] NVARCHAR(50) NOT NULL,
    CONSTRAINT FK_Drive_Site FOREIGN KEY ([MsSiteId]) REFERENCES ms.Site([Id]) ON DELETE CASCADE
);
GO

CREATE UNIQUE INDEX IX_Drive_DriveId ON ms.Drive ([DriveId]);
GO

CREATE INDEX IX_Drive_MsSiteId ON ms.Drive ([MsSiteId]);
GO


DROP PROCEDURE IF EXISTS CreateMsDrive;
GO

CREATE PROCEDURE CreateMsDrive
(
    @MsSiteId BIGINT,
    @DriveId NVARCHAR(255),
    @Name NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @DriveType NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO ms.Drive ([CreatedDatetime], [ModifiedDatetime], [MsSiteId], [DriveId], [Name], [WebUrl], [DriveType])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[MsSiteId],
        INSERTED.[DriveId],
        INSERTED.[Name],
        INSERTED.[WebUrl],
        INSERTED.[DriveType]
    VALUES (@Now, @Now, @MsSiteId, @DriveId, @Name, @WebUrl, @DriveType);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadMsDrives;
GO

CREATE PROCEDURE ReadMsDrives
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
        [MsSiteId],
        [DriveId],
        [Name],
        [WebUrl],
        [DriveType]
    FROM ms.Drive;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsDrives;

DROP PROCEDURE IF EXISTS ReadMsDrivesByMsSiteId;
GO

CREATE PROCEDURE ReadMsDrivesByMsSiteId
(
    @MsSiteId BIGINT
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
        [MsSiteId],
        [DriveId],
        [Name],
        [WebUrl],
        [DriveType]
    FROM ms.Drive
    WHERE [MsSiteId] = @MsSiteId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadMsDriveByPublicId;
GO

CREATE PROCEDURE ReadMsDriveByPublicId
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
        [MsSiteId],
        [DriveId],
        [Name],
        [WebUrl],
        [DriveType]
    FROM ms.Drive
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadMsDriveByDriveId;
GO

CREATE PROCEDURE ReadMsDriveByDriveId
(
    @DriveId NVARCHAR(255)
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
        [MsSiteId],
        [DriveId],
        [Name],
        [WebUrl],
        [DriveType]
    FROM ms.Drive
    WHERE [DriveId] = @DriveId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS UpdateMsDriveByPublicId;
GO

CREATE PROCEDURE UpdateMsDriveByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @MsSiteId BIGINT,
    @DriveId NVARCHAR(255),
    @Name NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @DriveType NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE ms.Drive
    SET [ModifiedDatetime] = @Now,
        [MsSiteId] = @MsSiteId,
        [DriveId] = @DriveId,
        [Name] = @Name,
        [WebUrl] = @WebUrl,
        [DriveType] = @DriveType
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[MsSiteId],
        INSERTED.[DriveId],
        INSERTED.[Name],
        INSERTED.[WebUrl],
        INSERTED.[DriveType]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteMsDriveByPublicId;
GO

CREATE PROCEDURE DeleteMsDriveByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM ms.Drive
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[MsSiteId],
        DELETED.[DriveId],
        DELETED.[Name],
        DELETED.[WebUrl],
        DELETED.[DriveType]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO
