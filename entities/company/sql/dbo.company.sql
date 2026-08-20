IF OBJECT_ID('dbo.Company', 'U') IS NULL
BEGIN
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
END
GO

-- Idempotent column add for existing environments (U-277). Live since
-- migration 238a_qbo_identity_headers.sql (2026-08); declared here so a
-- fresh environment built from just the base file matches prod — mirrors
-- entities/customer/sql/dbo.customer.sql's identical block for the same
-- migration family. (Pre-existing OrganizationId/CreatedByUserId/
-- ModifiedByUserId gap is unrelated and stays governed by README.md's
-- documented two-pass build order.)
IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Company') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Company] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Company') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Company] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Company_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Company')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Company_QboId_RealmId ON [dbo].[Company] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

CREATE OR ALTER PROCEDURE CreateCompany
(
    @Name NVARCHAR(50),
    @Website NVARCHAR(255),
    @OrganizationId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Company]
        ([CreatedDatetime], [ModifiedDatetime], [Name], [Website],
         [OrganizationId], [CreatedByUserId], [ModifiedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Website],
        INSERTED.[OrganizationId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    VALUES
        (@Now, @Now, @Name, @Website,
         @OrganizationId, @CreatedByUserId, COALESCE(@ModifiedByUserId, @CreatedByUserId));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadCompanies
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
        [Website],
        [OrganizationId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[Company]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadCompanyById
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
        [Website],
        [OrganizationId],
        [CreatedByUserId],
        [ModifiedByUserId],
        [QboId],
        [RealmId]
    FROM dbo.[Company]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- U-277 (Phase-4): direct dbo-native identity lookup, mirrors U-276's
-- ReadCustomerByQboIdAndRealmId. Lets the CompanyInfo connector resolve
-- "does a dbo.Company already exist for this external QBO id" WITHOUT
-- hopping through the qbo.CompanyInfo / qbo.CompanyInfoCompany
-- staging/mapping tables — every Company synced at least once already
-- carries QboId/RealmId via SetCompanyQboIdentity. RealmId NULL-equality
-- mirrors SetCompanyQboIdentity's own stolen-identity comparison.
CREATE OR ALTER PROCEDURE ReadCompanyByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50) = NULL
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
        [Website],
        [OrganizationId],
        [CreatedByUserId],
        [ModifiedByUserId],
        [QboId],
        [RealmId]
    FROM dbo.[Company]
    WHERE [QboId] = @QboId
      AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadCompanyByPublicId
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
        [Website],
        [OrganizationId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[Company]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadCompanyByName
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
        [Website],
        [OrganizationId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[Company]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateCompanyById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Website NVARCHAR(255),
    @OrganizationId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Company]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Website] = @Website,
        -- CASE WHEN guard so passing NULL preserves the existing
        -- OrganizationId during the Phase-0 → Phase-1 transition.
        -- Once the NOT NULL flip ships, callers must always supply it.
        [OrganizationId] = CASE WHEN @OrganizationId IS NULL THEN [OrganizationId] ELSE @OrganizationId END,
        [ModifiedByUserId] = @ModifiedByUserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Website],
        INSERTED.[OrganizationId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteCompanyById
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
        DELETED.[Website],
        DELETED.[OrganizationId],
        DELETED.[CreatedByUserId],
        DELETED.[ModifiedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Company_PublicId' AND object_id = OBJECT_ID('dbo.Company'))
BEGIN
    CREATE INDEX [IX_Company_PublicId] ON [dbo].[Company] ([PublicId]);
END
GO

CREATE OR ALTER PROCEDURE SetCompanyQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[Company]
        SET [QboId] = NULL, [RealmId] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[Company]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        @Stolen AS [Stolen]
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );
END;
GO
