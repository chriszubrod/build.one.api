IF OBJECT_ID('dbo.Organization', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Organization]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(255) NOT NULL,
    [Website] NVARCHAR(255) NULL
);
END
GO

GO


ALTER TABLE [dbo].[Organization]
ALTER COLUMN [RowVersion] BINARY(8) NOT NULL;
GO



GO

CREATE OR ALTER PROCEDURE CreateOrganization
(
    @Name NVARCHAR(255),
    @Website NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.Organization ([CreatedDatetime], [ModifiedDatetime], [Name], [Website])
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


GO

CREATE OR ALTER PROCEDURE ReadOrganizations
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
    FROM dbo.Organization
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;





GO

CREATE OR ALTER PROCEDURE ReadOrganizationById
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
    FROM dbo.Organization
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;





GO

CREATE OR ALTER PROCEDURE ReadOrganizationByPublicId
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
    FROM dbo.Organization
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;




GO

CREATE OR ALTER PROCEDURE ReadOrganizationByName
(
    @Name NVARCHAR(255)
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
    FROM dbo.Organization
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;





GO

CREATE OR ALTER PROCEDURE UpdateOrganizationById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(255),
    @Website NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.Organization
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




GO

CREATE OR ALTER PROCEDURE DeleteOrganizationById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.Organization
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

