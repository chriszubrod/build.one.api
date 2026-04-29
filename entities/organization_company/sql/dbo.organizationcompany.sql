IF OBJECT_ID('dbo.OrganizationCompany', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[OrganizationCompany]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [OrganizationId] BIGINT NOT NULL,
    [CompanyId] BIGINT NOT NULL
);
END
GO


CREATE OR ALTER PROCEDURE CreateOrganizationCompany
(
    @OrganizationId BIGINT,
    @CompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[OrganizationCompany] ([CreatedDatetime], [ModifiedDatetime], [OrganizationId], [CompanyId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[OrganizationId],
        INSERTED.[CompanyId]
    VALUES (@Now, @Now, @OrganizationId, @CompanyId);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationCompanies
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [OrganizationId],
        [CompanyId]
    FROM dbo.[OrganizationCompany]
    ORDER BY [OrganizationId] ASC, [CompanyId] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationCompanyById
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
        [OrganizationId],
        [CompanyId]
    FROM dbo.[OrganizationCompany]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationCompanyByPublicId
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
        [OrganizationId],
        [CompanyId]
    FROM dbo.[OrganizationCompany]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationCompaniesByOrganizationId
(
    @OrganizationId BIGINT
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
        [OrganizationId],
        [CompanyId]
    FROM dbo.[OrganizationCompany]
    WHERE [OrganizationId] = @OrganizationId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadOrganizationCompaniesByCompanyId
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
        [OrganizationId],
        [CompanyId]
    FROM dbo.[OrganizationCompany]
    WHERE [CompanyId] = @CompanyId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateOrganizationCompanyById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @OrganizationId BIGINT,
    @CompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[OrganizationCompany]
    SET
        [ModifiedDatetime] = @Now,
        [OrganizationId] = @OrganizationId,
        [CompanyId] = @CompanyId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[OrganizationId],
        INSERTED.[CompanyId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteOrganizationCompanyById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[OrganizationCompany]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[OrganizationId],
        DELETED.[CompanyId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_OrganizationCompany_Organization')
BEGIN
    ALTER TABLE [dbo].[OrganizationCompany] ADD CONSTRAINT [FK_OrganizationCompany_Organization] FOREIGN KEY ([OrganizationId]) REFERENCES [dbo].[Organization]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_OrganizationCompany_Company')
BEGIN
    ALTER TABLE [dbo].[OrganizationCompany] ADD CONSTRAINT [FK_OrganizationCompany_Company] FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

-- Prevent duplicate organization-company assignments
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_OrganizationCompany_OrganizationId_CompanyId' AND parent_object_id = OBJECT_ID('dbo.OrganizationCompany'))
BEGIN
    ALTER TABLE [dbo].[OrganizationCompany] ADD CONSTRAINT [UQ_OrganizationCompany_OrganizationId_CompanyId] UNIQUE ([OrganizationId], [CompanyId]);
END
GO
