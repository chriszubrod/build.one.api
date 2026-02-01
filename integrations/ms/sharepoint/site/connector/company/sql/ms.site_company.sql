GO

IF OBJECT_ID('ms.SiteCompany', 'U') IS NULL
BEGIN
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
END
GO


GO

CREATE OR ALTER PROCEDURE CreateSiteCompany
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


GO

CREATE OR ALTER PROCEDURE ReadSiteCompanyById
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


GO

CREATE OR ALTER PROCEDURE ReadSiteCompanyByCompanyId
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


GO

CREATE OR ALTER PROCEDURE ReadSiteCompanyBySiteId
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


GO

CREATE OR ALTER PROCEDURE DeleteSiteCompanyById
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


GO

CREATE OR ALTER PROCEDURE DeleteSiteCompanyByCompanyId
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

