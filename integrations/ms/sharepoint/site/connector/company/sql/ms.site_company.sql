DROP TABLE IF EXISTS [ms].[SiteCompany];
GO

CREATE TABLE [ms].[SiteCompany]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CompanyId] BIGINT NOT NULL,
    [SiteId] BIGINT NOT NULL,
    CONSTRAINT [UQ_SiteCompany_CompanyId] UNIQUE ([CompanyId]),
    CONSTRAINT [UQ_SiteCompany_SiteId] UNIQUE ([SiteId])
);
GO


DROP PROCEDURE IF EXISTS CreateSiteCompany;
GO

CREATE PROCEDURE CreateSiteCompany
(
    @CompanyId BIGINT,
    @SiteId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[SiteCompany] ([CreatedDatetime], [ModifiedDatetime], [CompanyId], [SiteId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CompanyId],
        INSERTED.[SiteId]
    VALUES (@Now, @Now, @CompanyId, @SiteId);

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSiteCompanyById;
GO

CREATE PROCEDURE ReadSiteCompanyById
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
        [CompanyId],
        [SiteId]
    FROM [ms].[SiteCompany]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSiteCompanyByCompanyId;
GO

CREATE PROCEDURE ReadSiteCompanyByCompanyId
(
    @CompanyId BIGINT
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
        [CompanyId],
        [SiteId]
    FROM [ms].[SiteCompany]
    WHERE [CompanyId] = @CompanyId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadSiteCompanyBySiteId;
GO

CREATE PROCEDURE ReadSiteCompanyBySiteId
(
    @SiteId BIGINT
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
        [CompanyId],
        [SiteId]
    FROM [ms].[SiteCompany]
    WHERE [SiteId] = @SiteId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteSiteCompanyById;
GO

CREATE PROCEDURE DeleteSiteCompanyById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[SiteCompany]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CompanyId],
        DELETED.[SiteId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteSiteCompanyByCompanyId;
GO

CREATE PROCEDURE DeleteSiteCompanyByCompanyId
(
    @CompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [ms].[SiteCompany]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[CompanyId],
        DELETED.[SiteId]
    WHERE [CompanyId] = @CompanyId;

    COMMIT TRANSACTION;
END;
GO

SELECT * FROM [ms].[SiteCompany];
