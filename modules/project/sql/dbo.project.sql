DROP TABLE IF EXISTS dbo.[Project];
GO




CREATE TABLE [dbo].[Project]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Description] NVARCHAR(500) NULL,
    [Status] NVARCHAR(50) NULL,
    [CustomerId] BIGINT NULL,
    [Abbreviation] NVARCHAR(20) NULL,
    CONSTRAINT [FK_Project_Customer] FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customer]([Id])
);
GO


DROP PROCEDURE IF EXISTS CreateProject;
GO

CREATE PROCEDURE CreateProject
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(500),
    @Status NVARCHAR(50),
    @CustomerId BIGINT NULL,
    @Abbreviation NVARCHAR(20) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Project] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [Status], [CustomerId], [Abbreviation])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[Status],
        INSERTED.[CustomerId],
        INSERTED.[Abbreviation]
    VALUES (@Now, @Now, @Name, @Description, @Status, @CustomerId, @Abbreviation);

    COMMIT TRANSACTION;
END;

EXEC CreateProject
    @Name = 'Sample Project',
    @Description = 'This is a sample project description',
    @Status = 'Active',
    @CustomerId = NULL;
GO


DROP PROCEDURE IF EXISTS ReadProjects;
GO

CREATE PROCEDURE ReadProjects
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadProjects;
GO


DROP PROCEDURE IF EXISTS ReadProjectById;
GO

CREATE PROCEDURE ReadProjectById
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadProjectById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadProjectByPublicId;
GO

CREATE PROCEDURE ReadProjectByPublicId
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadProjectByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadProjectByName;
GO

CREATE PROCEDURE ReadProjectByName
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadProjectByName
    @Name = 'Sample Project';
GO


DROP PROCEDURE IF EXISTS UpdateProjectById;
GO

CREATE PROCEDURE UpdateProjectById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Description NVARCHAR(500),
    @Status NVARCHAR(50),
    @CustomerId BIGINT NULL,
    @Abbreviation NVARCHAR(20) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Project]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [Status] = @Status,
        [CustomerId] = @CustomerId,
        [Abbreviation] = @Abbreviation
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[Status],
        INSERTED.[CustomerId],
        INSERTED.[Abbreviation]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateProjectById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @Name = 'Updated Project',
    @Description = 'This is an updated project description',
    @Status = 'In Progress',
    @CustomerId = NULL;
GO


DROP PROCEDURE IF EXISTS DeleteProjectById;
GO

CREATE PROCEDURE DeleteProjectById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Project]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[Status],
        DELETED.[CustomerId],
        DELETED.[Abbreviation]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteProjectById
    @Id = 3;
GO

SELECT * FROM dbo.Project;

ALTER TABLE [dbo].[Project]
ADD [Abbreviation] NVARCHAR(20) NULL;
GO