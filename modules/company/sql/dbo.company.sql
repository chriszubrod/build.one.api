CREATE TABLE [dbo].[Company]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Website] NVARCHAR(255) NULL
);
GO


DROP TABLE IF EXISTS dbo.[Company];
GO


DROP PROCEDURE IF EXISTS CreateCompany;
GO

CREATE PROCEDURE CreateCompany
(
    @Name NVARCHAR(50),
    @Website NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Company] ([CreatedDatetime], [ModifiedDatetime], [Name], [Website])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Website]
    VALUES (@Now, @Now, @Name, @Website);

    COMMIT TRANSACTION;
END;

EXEC CreateCompany
    @Name = 'Rogers Build, Inc.',
    @Website = 'https://www.rogersbuild.com';
GO


DROP PROCEDURE IF EXISTS ReadCompanies;
GO

CREATE PROCEDURE ReadCompanies
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
        [Website]
    FROM dbo.[Company]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadCompanies;
GO


DROP PROCEDURE IF EXISTS ReadCompanyById;
GO

CREATE PROCEDURE ReadCompanyById
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
        [Website]
    FROM dbo.[Company]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadCompanyById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadCompanyByPublicId;
GO

CREATE PROCEDURE ReadCompanyByPublicId
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
        [Website]
    FROM dbo.[Company]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadCompanyByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadCompanyByName;
GO

CREATE PROCEDURE ReadCompanyByName
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
        [Website]
    FROM dbo.[Company]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadCompanyByName
    @Name = 'BuildOne';
GO


DROP PROCEDURE IF EXISTS UpdateCompanyById;
GO

CREATE PROCEDURE UpdateCompanyById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Website NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Company]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Website] = @Website
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Website]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateCompanyById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @Name = 'BuildOne',
    @Website = 'https://buildone.com';
GO


DROP PROCEDURE IF EXISTS DeleteCompanyById;
GO

CREATE PROCEDURE DeleteCompanyById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Company]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Website]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteCompanyById
    @Id = 3;
GO
