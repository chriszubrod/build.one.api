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

-- U-281 (Phase-4 prerequisite, account family): dbo-native home for the one
-- live business fact BillBillConnector._get_ap_account_ref reads off
-- qbo.Account on every Bill push — "which QBO account is Accounts Payable
-- for this realm." Populated by QboAccountService.sync_from_qbo (the
-- existing scheduled qbo.Account pull), re-derived from the full local
-- qbo.Account mirror after every batch so it self-heals if QBO's AP account
-- ever changes — no separate timer, no manual/env-var upkeep. Widths mirror
-- qbo.Account's own [QboId]/[Name] columns.
IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Company') AND name = 'APAccountQboId')
BEGIN
    ALTER TABLE [dbo].[Company] ADD [APAccountQboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Company') AND name = 'APAccountName')
BEGIN
    ALTER TABLE [dbo].[Company] ADD [APAccountName] NVARCHAR(100) NULL;
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
        -- U-281: a genuine realm REASSIGNMENT (this row already carried a
        -- different, non-NULL RealmId) invalidates the AP-account cache
        -- stamped under the OLD realm — null it so _get_ap_account_ref
        -- falls back to a live qbo.Account scan until the next scheduled
        -- Account pull re-populates it correctly for the NEW realm. First-
        -- time stamping (old RealmId NULL) is not a reassignment and must
        -- leave these columns untouched (they're already NULL on a brand
        -- new row regardless). [RealmId] on the right-hand side below reads
        -- the PRE-update value — SQL Server evaluates every SET expression
        -- in one UPDATE against the row's original image, not against
        -- earlier assignments in the same SET list.
        [APAccountQboId] = CASE
            WHEN @RealmId IS NOT NULL AND [RealmId] IS NOT NULL AND [RealmId] <> @RealmId THEN NULL
            ELSE [APAccountQboId]
        END,
        [APAccountName] = CASE
            WHEN @RealmId IS NOT NULL AND [RealmId] IS NOT NULL AND [RealmId] <> @RealmId THEN NULL
            ELSE [APAccountName]
        END,
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

-- U-281: resolve a Company directly by QBO RealmId — the seam
-- _get_ap_account_ref needs (it only has realm_id at the Bill-push call
-- site, not a Company id/public_id). RealmId is unique per QBO-connected
-- Company in practice (stamped only via SetCompanyQboIdentity, always
-- alongside QboId), same assumption ReadCompanyByQboIdAndRealmId already
-- relies on for its (QboId, RealmId) pair.
CREATE OR ALTER PROCEDURE ReadCompanyByRealmId
(
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (1)
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
        [RealmId],
        [APAccountQboId],
        [APAccountName]
    FROM dbo.[Company]
    WHERE [RealmId] = @RealmId;
END;
GO

-- U-281: stamp the cached AP-account fact for the Company matching a realm.
-- Called once per qbo.Account pull (QboAccountService.sync_from_qbo), after
-- re-deriving "first Accounts-Payable-type account, Name ASC" from the full
-- local qbo.Account mirror — the exact selection _get_ap_account_ref used to
-- compute live on every Bill push. A plain overwrite (no CASE-WHEN NULL
-- guard): NULL here is the equally-valid "no Accounts Payable account
-- exists for this realm" answer, and must be allowed to replace a stale
-- non-NULL value, not be treated as "leave existing value alone."
CREATE OR ALTER PROCEDURE SetCompanyApAccount
(
    @RealmId NVARCHAR(50),
    @APAccountQboId NVARCHAR(50),
    @APAccountName NVARCHAR(100)
)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[Company]
    SET
        [APAccountQboId] = @APAccountQboId,
        [APAccountName] = @APAccountName,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[RealmId],
        INSERTED.[APAccountQboId],
        INSERTED.[APAccountName]
    WHERE [RealmId] = @RealmId;
END;
GO
