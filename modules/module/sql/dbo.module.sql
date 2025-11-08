CREATE TABLE [dbo].[Module]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Route] NVARCHAR(255) NOT NULL
);
GO


DROP TABLE IF EXISTS dbo.[Module];
GO


DROP PROCEDURE IF EXISTS CreateModule;
GO

CREATE PROCEDURE CreateModule
(
    @Name NVARCHAR(50),
    @Route NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Route]
    VALUES (@Now, @Now, @Name, @Route);

    COMMIT TRANSACTION;
END;

EXEC CreateModule
    @Name = 'Bills',
    @Route = '/bills';
GO


DROP PROCEDURE IF EXISTS ReadModules;
GO

CREATE PROCEDURE ReadModules
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Route]
    FROM dbo.[Module]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadModules;
GO


DROP PROCEDURE IF EXISTS ReadModuleById;
GO

CREATE PROCEDURE ReadModuleById
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
        [Name],
        [Route]
    FROM dbo.[Module]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadModuleByPublicId;
GO

CREATE PROCEDURE ReadModuleByPublicId
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
        [Name],
        [Route]
    FROM dbo.[Module]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadModuleByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadModuleByName;
GO

CREATE PROCEDURE ReadModuleByName
(
    @Name NVARCHAR(50)
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
        [Name],
        [Route]
    FROM dbo.[Module]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadModuleByName
    @Name = 'Module Name';
GO


DROP PROCEDURE IF EXISTS UpdateModuleById;
GO

CREATE PROCEDURE UpdateModuleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Route NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Module]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Route] = @Route
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Route]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateModuleById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @Name = 'Bills',
    @Route = '/bill/list';
GO


DROP PROCEDURE IF EXISTS DeleteModuleById;
GO

CREATE PROCEDURE DeleteModuleById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Module]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Route]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
