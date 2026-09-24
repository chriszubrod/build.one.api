-- Module registration for RBAC
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Assets')
BEGIN
    DECLARE @NowModule DATETIME2(3) = SYSUTCDATETIME();
    INSERT INTO dbo.[Module] ([Name], [Route], [CreatedDatetime], [ModifiedDatetime])
    VALUES ('Assets', '/asset/list', @NowModule, @NowModule);
END
GO

IF OBJECT_ID('dbo.Asset', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Asset]
(
    [Id] INT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_Asset_PublicId DEFAULT (NEWID()),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_Asset_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(200) NOT NULL,
    [AssetType] NVARCHAR(20) NOT NULL,
    [Make] NVARCHAR(100) NULL,
    [Model] NVARCHAR(100) NULL,
    [ModelYear] SMALLINT NULL,
    [SerialNumber] NVARCHAR(100) NULL,
    [Status] NVARCHAR(20) NOT NULL,
    [AcquisitionDate] DATE NULL,
    [DisposalDate] DATE NULL,
    -- Holds qbo.Account.QboId (QBO external id), NOT qbo.Account.Id (staging PK).
    [QboFixedAssetAccountId] NVARCHAR(50) NULL,
    -- Holds qbo.Account.QboId (QBO external id), NOT qbo.Account.Id (staging PK).
    [QboAccumDepAccountId] NVARCHAR(50) NULL,
    [CompanyId] INT NOT NULL,
    [CreatedByUserId] BIGINT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Asset_PublicId' AND object_id = OBJECT_ID('dbo.Asset'))
BEGIN
    CREATE UNIQUE INDEX UQ_Asset_PublicId ON dbo.[Asset] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Asset_CompanyId' AND object_id = OBJECT_ID('dbo.Asset'))
BEGIN
    CREATE INDEX IX_Asset_CompanyId ON dbo.[Asset] ([CompanyId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Asset_QboFixedAssetAccountId' AND object_id = OBJECT_ID('dbo.Asset'))
BEGIN
    CREATE INDEX IX_Asset_QboFixedAssetAccountId ON dbo.[Asset] ([QboFixedAssetAccountId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Asset_AssetType' AND parent_object_id = OBJECT_ID('dbo.Asset'))
BEGIN
    ALTER TABLE dbo.[Asset] ADD CONSTRAINT CK_Asset_AssetType
        CHECK ([AssetType] IN ('vehicle', 'machinery', 'equipment'));
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Asset_Status' AND parent_object_id = OBJECT_ID('dbo.Asset'))
BEGIN
    ALTER TABLE dbo.[Asset] ADD CONSTRAINT CK_Asset_Status
        CHECK ([Status] IN ('active', 'disposed'));
END
GO

IF OBJECT_ID('dbo.AssetFinancingNote', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[AssetFinancingNote]
(
    [Id] INT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_AssetFinancingNote_PublicId DEFAULT (NEWID()),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_AssetFinancingNote_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime] DATETIME2(3) NULL,
    [AssetId] INT NOT NULL,
    -- Holds qbo.Account.QboId (QBO external id), NOT qbo.Account.Id (staging PK).
    [QboLiabilityAccountId] NVARCHAR(50) NOT NULL,
    [CreatedByUserId] BIGINT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_AssetFinancingNote_PublicId' AND object_id = OBJECT_ID('dbo.AssetFinancingNote'))
BEGIN
    CREATE UNIQUE INDEX UQ_AssetFinancingNote_PublicId ON dbo.[AssetFinancingNote] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AssetFinancingNote_AssetId' AND object_id = OBJECT_ID('dbo.AssetFinancingNote'))
BEGIN
    CREATE INDEX IX_AssetFinancingNote_AssetId ON dbo.[AssetFinancingNote] ([AssetId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AssetFinancingNote_Asset')
BEGIN
    ALTER TABLE dbo.[AssetFinancingNote] ADD CONSTRAINT FK_AssetFinancingNote_Asset
        FOREIGN KEY ([AssetId]) REFERENCES dbo.[Asset]([Id]);
END
GO

IF OBJECT_ID('dbo.AssetAccountExclusion', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[AssetAccountExclusion]
(
    [Id] INT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_AssetAccountExclusion_PublicId DEFAULT (NEWID()),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL CONSTRAINT DF_AssetAccountExclusion_CreatedDatetime DEFAULT (SYSUTCDATETIME()),
    [ModifiedDatetime] DATETIME2(3) NULL,
    -- Holds qbo.Account.QboId (QBO external id), NOT qbo.Account.Id (staging PK).
    [QboAccountId] NVARCHAR(50) NOT NULL,
    [Reason] NVARCHAR(40) NOT NULL,
    [CompanyId] INT NOT NULL,
    [CreatedByUserId] BIGINT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_AssetAccountExclusion_PublicId' AND object_id = OBJECT_ID('dbo.AssetAccountExclusion'))
BEGIN
    CREATE UNIQUE INDEX UQ_AssetAccountExclusion_PublicId ON dbo.[AssetAccountExclusion] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AssetAccountExclusion_CompanyId_QboAccountId' AND object_id = OBJECT_ID('dbo.AssetAccountExclusion'))
BEGIN
    CREATE INDEX IX_AssetAccountExclusion_CompanyId_QboAccountId ON dbo.[AssetAccountExclusion] ([CompanyId], [QboAccountId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_AssetAccountExclusion_Reason' AND parent_object_id = OBJECT_ID('dbo.AssetAccountExclusion'))
BEGIN
    ALTER TABLE dbo.[AssetAccountExclusion] ADD CONSTRAINT CK_AssetAccountExclusion_Reason
        CHECK ([Reason] IN ('leasehold-improvement', 'parent-rollup-account'));
END
GO

CREATE OR ALTER PROCEDURE CreateAsset
(
    @Name NVARCHAR(200),
    @AssetType NVARCHAR(20),
    @Make NVARCHAR(100) = NULL,
    @Model NVARCHAR(100) = NULL,
    @ModelYear SMALLINT = NULL,
    @SerialNumber NVARCHAR(100) = NULL,
    @Status NVARCHAR(20) = 'active',
    @AcquisitionDate DATE = NULL,
    @DisposalDate DATE = NULL,
    @QboFixedAssetAccountId NVARCHAR(50) = NULL,
    @QboAccumDepAccountId NVARCHAR(50) = NULL,
    @CompanyId INT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    INSERT INTO dbo.[Asset]
        ([Name], [AssetType], [Make], [Model], [ModelYear], [SerialNumber], [Status],
         [AcquisitionDate], [DisposalDate], [QboFixedAssetAccountId], [QboAccumDepAccountId],
         [CompanyId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[AssetType],
        INSERTED.[Make],
        INSERTED.[Model],
        INSERTED.[ModelYear],
        INSERTED.[SerialNumber],
        INSERTED.[Status],
        CONVERT(VARCHAR(10), INSERTED.[AcquisitionDate], 23) AS [AcquisitionDate],
        CONVERT(VARCHAR(10), INSERTED.[DisposalDate], 23) AS [DisposalDate],
        INSERTED.[QboFixedAssetAccountId],
        INSERTED.[QboAccumDepAccountId],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId]
    VALUES
        (@Name, @AssetType, @Make, @Model, @ModelYear, @SerialNumber, @Status,
         @AcquisitionDate, @DisposalDate, @QboFixedAssetAccountId, @QboAccumDepAccountId,
         @CompanyId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetsByCompanyId
(
    @CompanyId INT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [AssetType],
        [Make],
        [Model],
        [ModelYear],
        [SerialNumber],
        [Status],
        CONVERT(VARCHAR(10), [AcquisitionDate], 23) AS [AcquisitionDate],
        CONVERT(VARCHAR(10), [DisposalDate], 23) AS [DisposalDate],
        [QboFixedAssetAccountId],
        [QboAccumDepAccountId],
        [CompanyId],
        [CreatedByUserId]
    FROM dbo.[Asset]
    WHERE [CompanyId] = @CompanyId
    ORDER BY [Name] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetById
(
    @Id INT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [AssetType],
        [Make],
        [Model],
        [ModelYear],
        [SerialNumber],
        [Status],
        CONVERT(VARCHAR(10), [AcquisitionDate], 23) AS [AcquisitionDate],
        CONVERT(VARCHAR(10), [DisposalDate], 23) AS [DisposalDate],
        [QboFixedAssetAccountId],
        [QboAccumDepAccountId],
        [CompanyId],
        [CreatedByUserId]
    FROM dbo.[Asset]
    WHERE [Id] = @Id;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [AssetType],
        [Make],
        [Model],
        [ModelYear],
        [SerialNumber],
        [Status],
        CONVERT(VARCHAR(10), [AcquisitionDate], 23) AS [AcquisitionDate],
        CONVERT(VARCHAR(10), [DisposalDate], 23) AS [DisposalDate],
        [QboFixedAssetAccountId],
        [QboAccumDepAccountId],
        [CompanyId],
        [CreatedByUserId]
    FROM dbo.[Asset]
    WHERE [PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetWithQboByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        a.[Id],
        a.[PublicId],
        a.[RowVersion],
        CONVERT(VARCHAR(19), a.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), a.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        a.[Name],
        a.[AssetType],
        a.[Make],
        a.[Model],
        a.[ModelYear],
        a.[SerialNumber],
        a.[Status],
        CONVERT(VARCHAR(10), a.[AcquisitionDate], 23) AS [AcquisitionDate],
        CONVERT(VARCHAR(10), a.[DisposalDate], 23) AS [DisposalDate],
        a.[QboFixedAssetAccountId],
        a.[QboAccumDepAccountId],
        a.[CompanyId],
        a.[CreatedByUserId],
        fa.[Name] AS [FixedAssetAccountName],
        fa.[CurrentBalance] AS [FixedAssetAccountBalance],
        accum.[Name] AS [AccumDepAccountName],
        accum.[CurrentBalance] AS [AccumDepAccountBalance]
    FROM dbo.[Asset] a
    INNER JOIN dbo.[Company] co ON co.[Id] = a.[CompanyId]
    LEFT JOIN qbo.[Account] fa
        ON fa.[QboId] = a.[QboFixedAssetAccountId]
       AND fa.[RealmId] = co.[RealmId]
    LEFT JOIN qbo.[Account] accum
        ON accum.[QboId] = a.[QboAccumDepAccountId]
       AND accum.[RealmId] = co.[RealmId]
    WHERE a.[PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE UpdateAssetById
(
    @Id INT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(200) = NULL,
    @AssetType NVARCHAR(20) = NULL,
    @Make NVARCHAR(100) = NULL,
    @Model NVARCHAR(100) = NULL,
    @ModelYear SMALLINT = NULL,
    @SerialNumber NVARCHAR(100) = NULL,
    @Status NVARCHAR(20) = NULL,
    @AcquisitionDate DATE = NULL,
    @DisposalDate DATE = NULL,
    @QboFixedAssetAccountId NVARCHAR(50) = NULL,
    @QboAccumDepAccountId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    UPDATE dbo.[Asset]
    SET
        [ModifiedDatetime] = SYSUTCDATETIME(),
        [Name] = CASE WHEN @Name IS NULL THEN [Name] ELSE @Name END,
        [AssetType] = CASE WHEN @AssetType IS NULL THEN [AssetType] ELSE @AssetType END,
        [Make] = CASE WHEN @Make IS NULL THEN [Make] ELSE @Make END,
        [Model] = CASE WHEN @Model IS NULL THEN [Model] ELSE @Model END,
        [ModelYear] = CASE WHEN @ModelYear IS NULL THEN [ModelYear] ELSE @ModelYear END,
        [SerialNumber] = CASE WHEN @SerialNumber IS NULL THEN [SerialNumber] ELSE @SerialNumber END,
        [Status] = CASE WHEN @Status IS NULL THEN [Status] ELSE @Status END,
        [AcquisitionDate] = CASE WHEN @AcquisitionDate IS NULL THEN [AcquisitionDate] ELSE @AcquisitionDate END,
        [DisposalDate] = CASE WHEN @DisposalDate IS NULL THEN [DisposalDate] ELSE @DisposalDate END,
        [QboFixedAssetAccountId] = CASE WHEN @QboFixedAssetAccountId IS NULL THEN [QboFixedAssetAccountId] ELSE @QboFixedAssetAccountId END,
        [QboAccumDepAccountId] = CASE WHEN @QboAccumDepAccountId IS NULL THEN [QboAccumDepAccountId] ELSE @QboAccumDepAccountId END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[AssetType],
        INSERTED.[Make],
        INSERTED.[Model],
        INSERTED.[ModelYear],
        INSERTED.[SerialNumber],
        INSERTED.[Status],
        CONVERT(VARCHAR(10), INSERTED.[AcquisitionDate], 23) AS [AcquisitionDate],
        CONVERT(VARCHAR(10), INSERTED.[DisposalDate], 23) AS [DisposalDate],
        INSERTED.[QboFixedAssetAccountId],
        INSERTED.[QboAccumDepAccountId],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteAssetCascadeById
(
    @Id INT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[AssetFinancingNote] WHERE [AssetId] = @Id;
    DELETE FROM dbo.[AssetAttachment] WHERE [AssetId] = @Id;

    DELETE FROM dbo.[Asset]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[AssetType],
        DELETED.[Make],
        DELETED.[Model],
        DELETED.[ModelYear],
        DELETED.[SerialNumber],
        DELETED.[Status],
        CONVERT(VARCHAR(10), DELETED.[AcquisitionDate], 23) AS [AcquisitionDate],
        CONVERT(VARCHAR(10), DELETED.[DisposalDate], 23) AS [DisposalDate],
        DELETED.[QboFixedAssetAccountId],
        DELETED.[QboAccumDepAccountId],
        DELETED.[CompanyId],
        DELETED.[CreatedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE CreateAssetFinancingNote
(
    @AssetId INT,
    @QboLiabilityAccountId NVARCHAR(50),
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    INSERT INTO dbo.[AssetFinancingNote]
        ([AssetId], [QboLiabilityAccountId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[AssetId],
        INSERTED.[QboLiabilityAccountId],
        INSERTED.[CreatedByUserId]
    VALUES
        (@AssetId, @QboLiabilityAccountId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetFinancingNotesByAssetId
(
    @AssetId INT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [AssetId],
        [QboLiabilityAccountId],
        [CreatedByUserId]
    FROM dbo.[AssetFinancingNote]
    WHERE [AssetId] = @AssetId
    ORDER BY [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetFinancingNoteByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [AssetId],
        [QboLiabilityAccountId],
        [CreatedByUserId]
    FROM dbo.[AssetFinancingNote]
    WHERE [PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE DeleteAssetFinancingNoteById
(
    @Id INT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[AssetFinancingNote]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[AssetId],
        DELETED.[QboLiabilityAccountId],
        DELETED.[CreatedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE CreateAssetAccountExclusion
(
    @QboAccountId NVARCHAR(50),
    @Reason NVARCHAR(40),
    @CompanyId INT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    INSERT INTO dbo.[AssetAccountExclusion]
        ([QboAccountId], [Reason], [CompanyId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboAccountId],
        INSERTED.[Reason],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId]
    VALUES
        (@QboAccountId, @Reason, @CompanyId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetAccountExclusionsByCompanyId
(
    @CompanyId INT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboAccountId],
        [Reason],
        [CompanyId],
        [CreatedByUserId]
    FROM dbo.[AssetAccountExclusion]
    WHERE [CompanyId] = @CompanyId
    ORDER BY [QboAccountId] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetAccountExclusionByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboAccountId],
        [Reason],
        [CompanyId],
        [CreatedByUserId]
    FROM dbo.[AssetAccountExclusion]
    WHERE [PublicId] = @PublicId;
END;
GO

CREATE OR ALTER PROCEDURE DeleteAssetAccountExclusionById
(
    @Id INT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DELETE FROM dbo.[AssetAccountExclusion]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboAccountId],
        DELETED.[Reason],
        DELETED.[CompanyId],
        DELETED.[CreatedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadAssetDivergenceCheck
(
    @CompanyId INT
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Set 1: QBO fixed-asset accounts with no Asset row and no exclusion.
    SELECT
        qa.[QboId] AS [QboAccountQboId],
        qa.[Name] AS [AccountName],
        qa.[CurrentBalance] AS [AccountBalance]
    FROM qbo.[Account] qa
    INNER JOIN dbo.[Company] co ON co.[Id] = @CompanyId AND qa.[RealmId] = co.[RealmId]
    WHERE qa.[AccountType] = N'Fixed Asset'
      AND ISNULL(qa.[Active], 1) = 1
      -- U-527: a $0 account with no Asset is NOT a divergence.
      --
      -- The QBO chart of accounts is upstream REFERENCE DATA. We read it; we do
      -- not control it and do not direct changes to it (Owner, 2026-09-23). So
      -- an account that corresponds to no asset we own, carries no value, and is
      -- not one of the two permitted exclusion reasons had NO remedy available
      -- to us at all -- making "target 0, continuous" unsatisfiable rather than
      -- demanding. Live example: acct 273 `2015 GMC Sierra (VIN506874)`, an empty
      -- duplicate mis-subtyped AccumulatedAmortization, superseded by acct
      -- 1150040025 which carries the real -$17,500 and is correctly linked.
      --
      -- This does NOT blind the check, because the condition is SELF-HEALING:
      -- the moment any value posts to such an account its balance goes non-zero
      -- and it re-enters Set 1. The drift this set exists to catch -- "a machine
      -- was bought or sold and nobody updated the register" -- arrives WITH a
      -- cost balance, so it is still caught. What is given up is the window
      -- between an account being created at $0 and its first posting.
      --
      -- ISNULL is defensive, not load-bearing: 0 of 48 active fixed-asset
      -- accounts carry a NULL balance today (measured 2026-09-23). Written this
      -- way so a future NULL is treated as "no value" rather than silently
      -- dropping the row through a NULL comparison.
      AND ISNULL(qa.[CurrentBalance], 0) <> 0
      -- An account is MAPPED if an Asset references it through EITHER column.
      -- Testing only QboFixedAssetAccountId reported every accumulated-depreciation
      -- account as unmapped: 20 false positives on the first live run, which masked
      -- the 2 genuine gaps the check exists to surface. A check that cries wolf on
      -- 22 of 24 rows gets ignored, so the false positives are the actual defect.
      AND NOT EXISTS (
          SELECT 1 FROM dbo.[Asset] a
          WHERE a.[CompanyId] = @CompanyId
            AND (a.[QboFixedAssetAccountId] = qa.[QboId]
              OR a.[QboAccumDepAccountId]  = qa.[QboId])
      )
      AND NOT EXISTS (
          SELECT 1 FROM dbo.[AssetAccountExclusion] e
          WHERE e.[CompanyId] = @CompanyId
            AND e.[QboAccountId] = qa.[QboId]
      )
    ORDER BY qa.[Name] ASC;

    -- Set 2: Asset rows whose QboFixedAssetAccountId no longer resolves in qbo.Account.
    SELECT
        a.[PublicId] AS [AssetPublicId],
        a.[Name] AS [AssetName],
        a.[QboFixedAssetAccountId] AS [QboFixedAssetAccountId]
    FROM dbo.[Asset] a
    WHERE a.[CompanyId] = @CompanyId
      AND a.[QboFixedAssetAccountId] IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM qbo.[Account] qa
          INNER JOIN dbo.[Company] co ON co.[Id] = a.[CompanyId] AND qa.[RealmId] = co.[RealmId]
          WHERE qa.[QboId] = a.[QboFixedAssetAccountId]
      )
    ORDER BY a.[Name] ASC;

    -- Set 3: Active assets whose fixed-asset QBO account balance is zero.
    SELECT
        a.[PublicId] AS [AssetPublicId],
        a.[Name] AS [AssetName],
        fa.[CurrentBalance] AS [FixedAssetAccountBalance]
    FROM dbo.[Asset] a
    INNER JOIN dbo.[Company] co ON co.[Id] = a.[CompanyId]
    INNER JOIN qbo.[Account] fa
        ON fa.[QboId] = a.[QboFixedAssetAccountId]
       AND fa.[RealmId] = co.[RealmId]
    WHERE a.[CompanyId] = @CompanyId
      AND a.[Status] = N'active'
      AND a.[QboFixedAssetAccountId] IS NOT NULL
      AND fa.[CurrentBalance] = 0
    ORDER BY a.[Name] ASC;
END;
GO
