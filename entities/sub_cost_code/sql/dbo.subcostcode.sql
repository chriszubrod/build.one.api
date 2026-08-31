-- ============================================================================
-- SubCostCode — Table
-- ============================================================================

IF OBJECT_ID('dbo.SubCostCode', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.SubCostCode
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWSEQUENTIALID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [Number] NVARCHAR(50) NOT NULL,
        [Name] NVARCHAR(255) NOT NULL,
        [Description] NVARCHAR(255) NULL,
        [CostCodeId] BIGINT NOT NULL,
        [Aliases] NVARCHAR(500) NULL
    );
END;
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.SubCostCode') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_SubCostCode_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_SubCostCode_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD CONSTRAINT [FK_SubCostCode_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- Migration: Add Aliases column if table already exists without it
IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.SubCostCode') AND name = 'Aliases')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [Aliases] NVARCHAR(500) NULL;
END
GO

-- FK constraint
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_SubCostCode_CostCode')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD CONSTRAINT [FK_SubCostCode_CostCode] FOREIGN KEY ([CostCodeId]) REFERENCES [dbo].[CostCode]([Id]);
END
GO

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SubCostCode_PublicId' AND object_id = OBJECT_ID('dbo.SubCostCode'))
BEGIN
    CREATE INDEX [IX_SubCostCode_PublicId] ON [dbo].[SubCostCode] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SubCostCode_CostCodeId' AND object_id = OBJECT_ID('dbo.SubCostCode'))
BEGIN
    CREATE INDEX [IX_SubCostCode_CostCodeId] ON [dbo].[SubCostCode] ([CostCodeId]) INCLUDE ([Number]);
END
GO

-- Add QboActive column if it does not exist (U-275: dbo-native mirror of
-- qbo.Item.Active, replacing vw_SubCostCode's read-side LEFT JOIN). NULL =
-- no QBO identity yet or not yet backfilled; populated at pull time via
-- SetSubCostCodeQboIdentity, backfilled for existing rows by
-- scripts/backfill_qbo_active_mirror.py.
IF COL_LENGTH('dbo.SubCostCode', 'QboActive') IS NULL
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [QboActive] BIT NULL;
END
GO

-- Idempotent column add for existing environments. Live since migration
-- 238c_qbo_identity_reference.sql; declared here so a fresh environment
-- built from just the base file matches prod (U-289, mirrors U-277's fix
-- for company/address).
IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.SubCostCode') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.SubCostCode') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_SubCostCode_QboId_RealmId' AND object_id = OBJECT_ID('dbo.SubCostCode')
)
BEGIN
    CREATE UNIQUE INDEX UQ_SubCostCode_QboId_RealmId ON [dbo].[SubCostCode] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO


-- ============================================================================
-- SubCostCode — View (single source of truth for column formatting)
-- ============================================================================
-- QboActive (U-255, dbo-native as of U-275) is a byproduct of every sproc
-- that resolves through this view, including
-- CreateSubCostCode/UpdateSubCostCodeById/DeleteSubCostCodeById/
-- UpsertSubCostCode — their mutation responses now carry it too, unlike
-- Vendor/PaymentTerm's Create/Update/Delete OUTPUT clauses, which deliberately
-- do NOT (T-SQL's OUTPUT clause on INSERT has no FROM/JOIN support at all, and
-- UPDATE/DELETE's OUTPUT...FROM...JOIN form is easy to get syntactically wrong
-- against the INSERTED/DELETED pseudo-tables — not worth the risk for a
-- cosmetic contract difference no consumer depends on yet). Accepted
-- inconsistency, not a bug — see TODO.md.

GO

CREATE OR ALTER VIEW [dbo].[vw_SubCostCode]
AS
    SELECT
        sc.[Id],
        sc.[PublicId],
        sc.[RowVersion],
        CONVERT(VARCHAR(19), sc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), sc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        sc.[Number],
        sc.[Name],
        sc.[Description],
        sc.[CostCodeId],
        sc.[Aliases],
        sc.[QboActive],
        sc.[QboId],
        sc.[RealmId]
    FROM dbo.[SubCostCode] sc;
GO


-- ============================================================================
-- SubCostCode — Stored Procedures
-- ============================================================================

CREATE OR ALTER PROCEDURE CreateSubCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CostCodeId BIGINT,
    @Aliases NVARCHAR(500) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[SubCostCode] ([CreatedDatetime], [ModifiedDatetime], [Number], [Name], [Description], [CostCodeId], [Aliases], [CreatedByUserId])
    VALUES (@Now, @Now, @Number, @Name, @Description, @CostCodeId, @Aliases, COALESCE(@CreatedByUserId, 17));

    SELECT * FROM dbo.[vw_SubCostCode] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadSubCostCodes
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_SubCostCode] ORDER BY [Number] ASC;
END;
GO


CREATE OR ALTER PROCEDURE ReadSubCostCodeById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_SubCostCode] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadSubCostCodeByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_SubCostCode] WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadSubCostCodeByNumber
(
    @Number NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_SubCostCode] WHERE [Number] = @Number;
END;
GO


CREATE OR ALTER PROCEDURE ReadSubCostCodeByCostCodeId
(
    @CostCodeId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_SubCostCode] WHERE [CostCodeId] = @CostCodeId ORDER BY [Number] ASC;
END;
GO


CREATE OR ALTER PROCEDURE ReadSubCostCodeByAlias
(
    @Alias NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1 *
    FROM dbo.[vw_SubCostCode]
    WHERE [Aliases] IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM STRING_SPLIT([Aliases], '|')
          WHERE LTRIM(RTRIM(value)) = LTRIM(RTRIM(@Alias))
      );
END;
GO


CREATE OR ALTER PROCEDURE UpdateSubCostCodeById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CostCodeId BIGINT,
    @Aliases NVARCHAR(500) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[SubCostCode]
    SET
        [ModifiedDatetime] = @Now,
        [Number] = @Number,
        [Name] = @Name,
        [Description] = @Description,
        [CostCodeId] = @CostCodeId,
        [Aliases] = @Aliases
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    IF @@ROWCOUNT > 0
        SELECT * FROM dbo.[vw_SubCostCode] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteSubCostCodeById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT * FROM dbo.[vw_SubCostCode] WHERE [Id] = @Id;

    DELETE FROM dbo.[SubCostCode] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- FindSubCostCodeForReply — single-call ranked SubCostCode lookup for
-- reviewer-reply parsing. PMs reply with shorthand ("13.1") that doesn't
-- always match Number exactly ("13.01"). Strategies:
--
--   1.00  exact_number             — Number = @Hint
--   0.95  exact_number_normalized  — segment-pad each "."-delimited part
--                                    to 2 digits (so "13.1" → "13.01")
--   0.90  exact_alias              — pipe-delimited Alias matches @Hint
--   0.80  substring_alias          — Aliases CONTAINS @Hint
--   0.75  substring_name           — Name CONTAINS @Hint
--
-- Each SubCostCode row appears at most once at its highest-scoring
-- strategy. Returns up to 3 candidates ordered by confidence desc.
-- ============================================================================

-- ============================================================================
-- FindSubCostCodeForReply — single-call ranked SubCostCode lookup for
-- reviewer-reply parsing. PMs reply with shorthand ("13.1") that doesn't
-- always match Number exactly ("13.01"). Strategies:
--
--   1.00  exact_number             — Number = @Hint
--   0.95  exact_number_normalized  — segment-pad each "."-delimited part
--                                    to 2 digits (so "13.1" → "13.01")
--   0.90  exact_alias              — pipe-delimited Alias matches @Hint
--   0.80  substring_alias          — Aliases CONTAINS @Hint
--   0.75  substring_name           — Name CONTAINS @Hint
--
-- Each SubCostCode row appears at most once at its highest-scoring
-- strategy. Returns up to 3 candidates ordered by confidence desc.
-- ============================================================================

CREATE OR ALTER PROCEDURE FindSubCostCodeForReply
(
    @Hint NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @HintNorm NVARCHAR(255) = LTRIM(RTRIM(ISNULL(@Hint, '')));

    -- Right-segment pad helper: SubCostCode.Number format is
    -- "<variable-left>.<2-digit-right>" (e.g. "1.00", "13.01",
    -- "10.05"). PMs commonly drop trailing zeros ("13.1" instead of
    -- "13.01", "6.0" instead of "6.00"). Pad the right segment to 2
    -- chars with leading zeros; leave the left segment as-is. Single
    -- dot only (the convention).
    DECLARE @PaddedHint NVARCHAR(255) = NULL;
    IF @HintNorm <> '' AND CHARINDEX('.', @HintNorm) > 0
    BEGIN
        DECLARE @LeftPart NVARCHAR(50) = LEFT(@HintNorm, CHARINDEX('.', @HintNorm) - 1);
        DECLARE @RightPart NVARCHAR(50) = SUBSTRING(@HintNorm, CHARINDEX('.', @HintNorm) + 1, 250);
        IF LEN(@RightPart) = 1 SET @RightPart = '0' + @RightPart;
        SET @PaddedHint = @LeftPart + '.' + @RightPart;
    END

    -- LIKE escapes
    DECLARE @HintLike NVARCHAR(257) = REPLACE(REPLACE(LOWER(@HintNorm), '%', '[%]'), '_', '[_]');

    ;WITH
    exact_number AS (
        SELECT [Id], CAST(1.00 AS DECIMAL(3,2)) AS Confidence,
               CAST('exact_number' AS NVARCHAR(50)) AS Strategy,
               [Number] AS MatchedTerm
        FROM dbo.[SubCostCode]
        WHERE @HintNorm <> '' AND [Number] = @HintNorm
    ),
    exact_normalized AS (
        SELECT [Id], CAST(0.95 AS DECIMAL(3,2)) AS Confidence,
               CAST('exact_number_normalized' AS NVARCHAR(50)) AS Strategy,
               [Number] AS MatchedTerm
        FROM dbo.[SubCostCode]
        WHERE @PaddedHint IS NOT NULL AND [Number] = @PaddedHint
    ),
    exact_alias AS (
        SELECT scc.[Id], CAST(0.90 AS DECIMAL(3,2)) AS Confidence,
               CAST('exact_alias' AS NVARCHAR(50)) AS Strategy,
               LTRIM(RTRIM(s.value)) AS MatchedTerm
        FROM dbo.[SubCostCode] scc
        CROSS APPLY STRING_SPLIT(ISNULL(scc.[Aliases], ''), '|') s
        WHERE @HintNorm <> '' AND LTRIM(RTRIM(s.value)) = @HintNorm
    ),
    substring_alias AS (
        SELECT [Id], CAST(0.80 AS DECIMAL(3,2)) AS Confidence,
               CAST('substring_alias' AS NVARCHAR(50)) AS Strategy,
               [Aliases] AS MatchedTerm
        FROM dbo.[SubCostCode]
        WHERE @HintNorm <> '' AND [Aliases] IS NOT NULL
          AND LOWER([Aliases]) LIKE '%' + @HintLike + '%'
    ),
    substring_name AS (
        SELECT [Id], CAST(0.75 AS DECIMAL(3,2)) AS Confidence,
               CAST('substring_name' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[SubCostCode]
        WHERE @HintNorm <> ''
          AND LOWER([Name]) LIKE '%' + @HintLike + '%'
    ),
    all_candidates AS (
        SELECT * FROM exact_number
        UNION ALL SELECT * FROM exact_normalized
        UNION ALL SELECT * FROM exact_alias
        UNION ALL SELECT * FROM substring_alias
        UNION ALL SELECT * FROM substring_name
    ),
    ranked AS (
        SELECT [Id], Confidence, Strategy, MatchedTerm,
               ROW_NUMBER() OVER (PARTITION BY [Id] ORDER BY Confidence DESC) AS rn
        FROM all_candidates
    )
    SELECT TOP 3
        scc.[Id]                              AS SubCostCodeId,
        CAST(scc.[PublicId] AS NVARCHAR(36))  AS SubCostCodePublicId,
        scc.[Number]                          AS Number,
        scc.[Name]                            AS Name,
        scc.[CostCodeId]                      AS CostCodeId,
        scc.[Aliases]                         AS Aliases,
        r.Confidence                          AS Confidence,
        r.Strategy                            AS Strategy,
        r.MatchedTerm                         AS MatchedTerm
    FROM ranked r
    INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = r.[Id]
    WHERE r.rn = 1
    ORDER BY r.Confidence DESC, scc.[Number] ASC;
END;
GO


-- Upsert by Number + CostCodeId (for import flows)
-- Upsert by Number + CostCodeId (for import flows)
CREATE OR ALTER PROCEDURE UpsertSubCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CostCodeId BIGINT,
    @Aliases NVARCHAR(500) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[SubCostCode] AS target
    USING (SELECT @Number AS Number, @CostCodeId AS CostCodeId) AS source
    ON target.[Number] = source.Number AND target.[CostCodeId] = source.CostCodeId

    WHEN MATCHED THEN
        UPDATE SET
            [ModifiedDatetime] = @Now,
            [Name] = @Name,
            [Description] = COALESCE(@Description, target.[Description]),
            [Aliases] = COALESCE(@Aliases, target.[Aliases])

    WHEN NOT MATCHED THEN
        INSERT ([CreatedDatetime], [ModifiedDatetime], [Number], [Name], [Description], [CostCodeId], [Aliases])
        VALUES (@Now, @Now, @Number, @Name, @Description, @CostCodeId, @Aliases);

    -- Return the upserted row
    SELECT * FROM dbo.[vw_SubCostCode]
    WHERE [Number] = @Number AND [CostCodeId] = @CostCodeId;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetSubCostCodeQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50) = NULL,
    @RealmId NVARCHAR(50) = NULL,
    @Active BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[SubCostCode]
        SET [QboId] = NULL, [RealmId] = NULL, [QboActive] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[SubCostCode]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [QboActive] = CASE WHEN @Active IS NOT NULL THEN @Active ELSE [QboActive] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        INSERTED.[QboActive],
        @Stolen AS [Stolen]
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
         OR (@Active IS NOT NULL AND ([QboActive] IS NULL OR [QboActive] <> @Active))
      );
END;
GO

-- U-289 (Phase-4 repoint): direct dbo-native identity lookup. Lets the item
-- connector resolve "does a dbo.SubCostCode already exist for this external
-- QBO item id" WITHOUT hopping through the qbo.ItemSubCostCode mapping
-- table — every SubCostCode synced at least once already carries
-- QboId/RealmId via SetSubCostCodeQboIdentity, so this is the steady-state
-- fast path; the mapping-table lookup remains as a fallback for rows that
-- predate identity stamping. RealmId NULL-equality mirrors
-- SetSubCostCodeQboIdentity's own stolen-identity comparison.
CREATE OR ALTER PROCEDURE ReadSubCostCodeByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_SubCostCode]
    WHERE [QboId] = @QboId
      AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));
END;
GO
