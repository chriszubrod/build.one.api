IF OBJECT_ID('dbo.Module', 'U') IS NULL
BEGIN
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
END
GO


GO


GO

CREATE OR ALTER PROCEDURE CreateModule
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

-- Note: Workflow Inbox module is created in agents/persistence/sql/seed.WorkflowInboxModule.sql
GO


GO

CREATE OR ALTER PROCEDURE ReadModules
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



GO

CREATE OR ALTER PROCEDURE ReadModuleById
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



GO

CREATE OR ALTER PROCEDURE ReadModuleByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadModuleByName
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



GO

CREATE OR ALTER PROCEDURE UpdateModuleById
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



GO

CREATE OR ALTER PROCEDURE DeleteModuleById
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

