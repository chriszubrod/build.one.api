IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ms')
    EXEC('CREATE SCHEMA ms AUTHORIZATION dbo;');
GO

IF OBJECT_ID('ms.Site', 'U') IS NOT NULL
    DROP TABLE ms.Site;
GO

CREATE TABLE ms.Site
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [SiteId] NVARCHAR(255) NOT NULL,
    [DisplayName] NVARCHAR(255) NOT NULL,
    [WebUrl] NVARCHAR(MAX) NOT NULL,
    [Hostname] NVARCHAR(255) NOT NULL
);
GO

CREATE UNIQUE INDEX IX_Site_SiteId ON ms.Site ([SiteId]);
GO

CREATE INDEX IX_Site_Hostname ON ms.Site ([Hostname]);
GO


DROP PROCEDURE IF EXISTS CreateMsSite;
GO

CREATE PROCEDURE CreateMsSite
(
    @SiteId NVARCHAR(255),
    @DisplayName NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @Hostname NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO ms.Site ([CreatedDatetime], [ModifiedDatetime], [SiteId], [DisplayName], [WebUrl], [Hostname])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[SiteId],
        INSERTED.[DisplayName],
        INSERTED.[WebUrl],
        INSERTED.[Hostname]
    VALUES (@Now, @Now, @SiteId, @DisplayName, @WebUrl, @Hostname);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadMsSites;
GO

CREATE PROCEDURE ReadMsSites
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
        [SiteId],
        [DisplayName],
        [WebUrl],
        [Hostname]
    FROM ms.Site;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsSites;

DROP PROCEDURE IF EXISTS ReadMsSiteByPublicId;
GO

CREATE PROCEDURE ReadMsSiteByPublicId
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
        [SiteId],
        [DisplayName],
        [WebUrl],
        [Hostname]
    FROM ms.Site
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadMsSiteBySiteId;
GO

CREATE PROCEDURE ReadMsSiteBySiteId
(
    @SiteId NVARCHAR(255)
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
        [SiteId],
        [DisplayName],
        [WebUrl],
        [Hostname]
    FROM ms.Site
    WHERE [SiteId] = @SiteId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS UpdateMsSiteByPublicId;
GO

CREATE PROCEDURE UpdateMsSiteByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @SiteId NVARCHAR(255),
    @DisplayName NVARCHAR(255),
    @WebUrl NVARCHAR(MAX),
    @Hostname NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE ms.Site
    SET [ModifiedDatetime] = @Now,
        [SiteId] = @SiteId,
        [DisplayName] = @DisplayName,
        [WebUrl] = @WebUrl,
        [Hostname] = @Hostname
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[SiteId],
        INSERTED.[DisplayName],
        INSERTED.[WebUrl],
        INSERTED.[Hostname]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteMsSiteByPublicId;
GO

CREATE PROCEDURE DeleteMsSiteByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM ms.Site
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[SiteId],
        DELETED.[DisplayName],
        DELETED.[WebUrl],
        DELETED.[Hostname]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO
