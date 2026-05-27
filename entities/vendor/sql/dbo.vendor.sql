GO

IF OBJECT_ID('dbo.Vendor', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Vendor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(450) NOT NULL,
    [Abbreviation] NVARCHAR(255) NULL,
    [VendorTypeId] BIGINT NULL,
    [TaxpayerId] BIGINT NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    [IsDeleted] BIT NOT NULL DEFAULT 0,
    [IsContractLabor] BIT NOT NULL DEFAULT 0,
    -- Free-text per-vendor notes — visible in the React Vendor edit
    -- page and surfaced to the bill_specialist agent via
    -- FindVendorForInvoice. Use for vendor-specific quirks the agent
    -- should apply when creating bills — e.g. "trim /N suffix from
    -- invoice numbers" for Walker Lumber, or context like "ar@…
    -- forwards from concept@…". Free-text on purpose; structured rules
    -- can come later when patterns stabilize.
    [Notes] NVARCHAR(MAX) NULL
);
END
GO

-- Idempotent column add for existing environments. Renames the
-- legacy 'IntakeNotes' column when present so the data + UX label
-- stay aligned ('Notes' is the user-facing name).
IF OBJECT_ID('dbo.Vendor', 'U') IS NOT NULL AND EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'IntakeNotes' AND Object_ID = OBJECT_ID('dbo.Vendor')
) AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'Notes' AND Object_ID = OBJECT_ID('dbo.Vendor')
)
BEGIN
    EXEC sp_rename 'dbo.Vendor.IntakeNotes', 'Notes', 'COLUMN';
END
GO

IF OBJECT_ID('dbo.Vendor', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'Notes' AND Object_ID = OBJECT_ID('dbo.Vendor')
)
BEGIN
    ALTER TABLE dbo.[Vendor] ADD [Notes] NVARCHAR(MAX) NULL;
END
GO

-- Migrate Name column from NVARCHAR(MAX) to NVARCHAR(450) for indexability
IF EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Vendor' AND TABLE_SCHEMA = 'dbo' AND COLUMN_NAME = 'Name' AND CHARACTER_MAXIMUM_LENGTH = -1
)
BEGIN
    ALTER TABLE [dbo].[Vendor] ALTER COLUMN [Name] NVARCHAR(450) NOT NULL;
END
GO

-- Add IsDeleted column if it does not exist (migration for existing tables)
IF COL_LENGTH('dbo.Vendor', 'IsDeleted') IS NULL
BEGIN
    ALTER TABLE [dbo].[Vendor] ADD [IsDeleted] BIT NOT NULL DEFAULT 0;
END
GO

-- Add IsContractLabor column if it does not exist (migration for existing tables)
IF COL_LENGTH('dbo.Vendor', 'IsContractLabor') IS NULL
BEGIN
    ALTER TABLE [dbo].[Vendor] ADD [IsContractLabor] BIT NOT NULL DEFAULT 0;
END
GO

-- FK constraint: VendorTypeId -> VendorType.Id
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Vendor_VendorType')
BEGIN
    ALTER TABLE [dbo].[Vendor]
    ADD CONSTRAINT [FK_Vendor_VendorType] FOREIGN KEY ([VendorTypeId]) REFERENCES [dbo].[VendorType]([Id]);
END
GO

-- FK constraint: TaxpayerId -> Taxpayer.Id
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Vendor_Taxpayer')
BEGIN
    ALTER TABLE [dbo].[Vendor]
    ADD CONSTRAINT [FK_Vendor_Taxpayer] FOREIGN KEY ([TaxpayerId]) REFERENCES [dbo].[Taxpayer]([Id]);
END
GO

-- Unique index on Name for active (non-deleted) vendors
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Vendor_Name_Active' AND object_id = OBJECT_ID('dbo.Vendor'))
BEGIN
    CREATE UNIQUE INDEX [UQ_Vendor_Name_Active] ON [dbo].[Vendor] ([Name]) WHERE [IsDeleted] = 0;
END
GO


GO

CREATE OR ALTER PROCEDURE CreateVendor
(
    @Name NVARCHAR(450),
    @Abbreviation NVARCHAR(255),
    @VendorTypeId BIGINT NULL,
    @TaxpayerId BIGINT NULL,
    @IsDraft BIT = 1,
    @IsContractLabor BIT = 0,
    @Notes NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Vendor] ([CreatedDatetime], [ModifiedDatetime], [Name], [Abbreviation], [VendorTypeId], [TaxpayerId], [IsDraft], [IsDeleted], [IsContractLabor], [Notes], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation],
        INSERTED.[VendorTypeId],
        INSERTED.[TaxpayerId],
        INSERTED.[IsDraft],
        INSERTED.[IsDeleted],
        INSERTED.[IsContractLabor],
        INSERTED.[Notes]
    VALUES (@Now, @Now, @Name, @Abbreviation, @VendorTypeId, @TaxpayerId, @IsDraft, 0, @IsContractLabor, @Notes, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendors
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor],
        [Notes]
    FROM dbo.[Vendor]
    WHERE [IsDeleted] = 0
    ORDER BY [Name] ASC;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendorById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor],
        [Notes]
    FROM dbo.[Vendor]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendorByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor],
        [Notes]
    FROM dbo.[Vendor]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE ReadVendorByName
(
    @Name NVARCHAR(450)
)
AS
BEGIN
    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Abbreviation],
        [VendorTypeId],
        [TaxpayerId],
        [IsDraft],
        [IsDeleted],
        [IsContractLabor],
        [Notes]
    FROM dbo.[Vendor]
    WHERE [Name] = @Name AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE UpdateVendorById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(450),
    @Abbreviation NVARCHAR(255),
    @VendorTypeId BIGINT NULL,
    @TaxpayerId BIGINT NULL,
    @IsDraft BIT = NULL,
    @IsContractLabor BIT = NULL,
    @Notes NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Verify the record exists and is not deleted
    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [Id] = @Id AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Vendor not found.', 16, 1);
        RETURN;
    END

    -- Verify RowVersion matches (optimistic concurrency check)
    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: the vendor record has been modified by another user. Please refresh and try again.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Vendor]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Abbreviation] = @Abbreviation,
        [VendorTypeId] = CASE WHEN @VendorTypeId IS NULL THEN [VendorTypeId] ELSE @VendorTypeId END,
        [TaxpayerId] = CASE WHEN @TaxpayerId IS NULL THEN [TaxpayerId] ELSE @TaxpayerId END,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END,
        [IsContractLabor] = CASE WHEN @IsContractLabor IS NULL THEN [IsContractLabor] ELSE @IsContractLabor END,
        [Notes] = @Notes
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation],
        INSERTED.[VendorTypeId],
        INSERTED.[TaxpayerId],
        INSERTED.[IsDraft],
        INSERTED.[IsDeleted],
        INSERTED.[IsContractLabor],
        INSERTED.[Notes]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE SoftDeleteVendorByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [PublicId] = @PublicId AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Vendor not found.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Vendor]
    SET
        [ModifiedDatetime] = @Now,
        [IsDeleted] = 1
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation],
        INSERTED.[VendorTypeId],
        INSERTED.[TaxpayerId],
        INSERTED.[IsDraft],
        INSERTED.[IsDeleted],
        INSERTED.[IsContractLabor],
        INSERTED.[Notes]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO

-- ============================================================================
-- FindVendorForInvoice — single-call multi-strategy ranked vendor lookup for
-- invoice classification. Designed for the bill_specialist agent so it doesn't
-- have to retry search_vendors with progressively-shorter substrings.
--
-- Strategies (descending confidence):
--   1.00  domain_contact       — Vendor has a Contact whose Email ends in @<sender_domain>
--   0.95  exact_name           — case-insensitive Name == @VendorName
--   0.90  exact_abbreviation   — case-insensitive Abbreviation == @VendorName
--   0.85  prefix_name          — Name STARTS WITH first 2 words of @VendorName
--   0.75  substring_two_words  — Name CONTAINS first 2 words of @VendorName
--   0.65  substring_first_word — Name CONTAINS first word of @VendorName
--
-- Each Vendor row appears at most once — at its highest-scoring strategy.
-- Returns up to 5 candidates ordered by confidence desc.
--
-- Caller passes BOTH @VendorName and (optionally) @SenderDomain. Either may
-- match independently — passing both increases recall.
-- ============================================================================

CREATE OR ALTER PROCEDURE FindVendorForInvoice
(
    @VendorName NVARCHAR(450),
    @SenderDomain NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @NameNorm NVARCHAR(450) = LTRIM(RTRIM(ISNULL(@VendorName, '')));
    DECLARE @DomainNorm NVARCHAR(255) = LTRIM(RTRIM(LOWER(ISNULL(@SenderDomain, ''))));

    -- Extract the first word and the first two words for substring/prefix
    -- strategies. Defaults to the full @NameNorm when there are fewer
    -- than two words.
    DECLARE @FirstSpace INT = NULLIF(CHARINDEX(' ', @NameNorm), 0);
    DECLARE @SecondSpace INT = NULLIF(CHARINDEX(' ', @NameNorm, ISNULL(@FirstSpace, 0) + 1), 0);

    DECLARE @FirstWord NVARCHAR(255) =
        CASE WHEN @FirstSpace IS NOT NULL THEN LEFT(@NameNorm, @FirstSpace - 1)
             ELSE @NameNorm END;
    DECLARE @TwoWords NVARCHAR(255) =
        CASE WHEN @SecondSpace IS NOT NULL THEN LEFT(@NameNorm, @SecondSpace - 1)
             ELSE @NameNorm END;

    -- Escape % and _ in the LIKE patterns so vendor names with literal
    -- wildcards (rare) don't accidentally pattern-match.
    DECLARE @TwoWordsLike NVARCHAR(257) = REPLACE(REPLACE(LOWER(@TwoWords), '%', '[%]'), '_', '[_]');
    DECLARE @FirstWordLike NVARCHAR(257) = REPLACE(REPLACE(LOWER(@FirstWord), '%', '[%]'), '_', '[_]');

    ;WITH
    domain_match AS (
        SELECT DISTINCT v.[Id],
               CAST(1.00 AS DECIMAL(3,2)) AS Confidence,
               CAST('domain_contact' AS NVARCHAR(50)) AS Strategy,
               c.[Email] AS MatchedTerm
        FROM dbo.[Vendor] v
        INNER JOIN dbo.[Contact] c ON c.[VendorId] = v.[Id]
        WHERE v.[IsDeleted] = 0
          AND @DomainNorm <> ''
          AND c.[Email] IS NOT NULL
          AND LOWER(c.[Email]) LIKE '%@' + @DomainNorm
    ),
    exact_name AS (
        SELECT [Id], CAST(0.95 AS DECIMAL(3,2)) AS Confidence,
               CAST('exact_name' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Vendor]
        WHERE [IsDeleted] = 0 AND @NameNorm <> ''
          AND LOWER([Name]) = LOWER(@NameNorm)
    ),
    exact_abbr AS (
        SELECT [Id], CAST(0.90 AS DECIMAL(3,2)) AS Confidence,
               CAST('exact_abbreviation' AS NVARCHAR(50)) AS Strategy,
               [Abbreviation] AS MatchedTerm
        FROM dbo.[Vendor]
        WHERE [IsDeleted] = 0 AND @NameNorm <> ''
          AND [Abbreviation] IS NOT NULL
          AND LOWER([Abbreviation]) = LOWER(@NameNorm)
    ),
    prefix_two AS (
        SELECT [Id], CAST(0.85 AS DECIMAL(3,2)) AS Confidence,
               CAST('prefix_name' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Vendor]
        WHERE [IsDeleted] = 0 AND @TwoWords <> ''
          AND LOWER([Name]) LIKE @TwoWordsLike + '%'
    ),
    substring_two AS (
        SELECT [Id], CAST(0.75 AS DECIMAL(3,2)) AS Confidence,
               CAST('substring_two_words' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Vendor]
        WHERE [IsDeleted] = 0 AND @TwoWords <> ''
          AND LOWER([Name]) LIKE '%' + @TwoWordsLike + '%'
    ),
    substring_one AS (
        SELECT [Id], CAST(0.65 AS DECIMAL(3,2)) AS Confidence,
               CAST('substring_first_word' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Vendor]
        WHERE [IsDeleted] = 0 AND @FirstWord <> ''
          AND LOWER([Name]) LIKE '%' + @FirstWordLike + '%'
    ),
    all_candidates AS (
        SELECT * FROM domain_match
        UNION ALL SELECT * FROM exact_name
        UNION ALL SELECT * FROM exact_abbr
        UNION ALL SELECT * FROM prefix_two
        UNION ALL SELECT * FROM substring_two
        UNION ALL SELECT * FROM substring_one
    ),
    -- Per-vendor pick the highest-confidence strategy that matched.
    ranked AS (
        SELECT [Id], Confidence, Strategy, MatchedTerm,
               ROW_NUMBER() OVER (PARTITION BY [Id] ORDER BY Confidence DESC) AS rn
        FROM all_candidates
    )
    SELECT TOP 5
        v.[Id]                              AS VendorId,
        CAST(v.[PublicId] AS NVARCHAR(36))  AS VendorPublicId,
        v.[Name]                            AS VendorName,
        v.[Abbreviation]                    AS Abbreviation,
        v.[IsDraft]                         AS IsDraft,
        v.[Notes]                           AS Notes,
        r.Confidence                        AS Confidence,
        r.Strategy                          AS Strategy,
        r.MatchedTerm                       AS MatchedTerm
    FROM ranked r
    INNER JOIN dbo.[Vendor] v ON v.[Id] = r.[Id]
    WHERE r.rn = 1
    ORDER BY r.Confidence DESC, v.[Name] ASC;
END;
GO


-- ─────────────────────────────────────────────────────────────────────
-- FindContractLaborVendorByEmail — sender-keyed lookup for the
-- contract_labor_specialist agent. Binds a worker's email address back
-- to the Vendor row carrying their IsContractLabor flag.
--
-- Migration counterpart: entities/vendor/sql/migrations/001_find_contract_labor_vendor_by_email.sql
-- (keep these in sync — re-running the canonical file must match the
-- migration's body).
-- ─────────────────────────────────────────────────────────────────────

CREATE OR ALTER PROCEDURE FindContractLaborVendorByEmail
(
    @SenderEmail NVARCHAR(320)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1
        v.[Id],
        v.[PublicId],
        v.[RowVersion],
        CONVERT(VARCHAR(19), v.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), v.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        v.[Name],
        v.[Abbreviation],
        v.[VendorTypeId],
        v.[TaxpayerId],
        v.[IsDraft],
        v.[IsDeleted],
        v.[IsContractLabor],
        v.[Notes]
    FROM dbo.[Vendor] v
    INNER JOIN dbo.[Contact] c ON c.[VendorId] = v.[Id]
    WHERE v.[IsContractLabor] = 1
      AND v.[IsDeleted]       = 0
      AND LOWER(c.[Email])    = LOWER(@SenderEmail)
    ORDER BY v.[Id];
END;
