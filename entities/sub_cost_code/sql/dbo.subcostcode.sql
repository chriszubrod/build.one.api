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


-- ============================================================================
-- SubCostCode — View (single source of truth for column formatting)
-- ============================================================================

GO

CREATE OR ALTER VIEW [dbo].[vw_SubCostCode]
AS
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Number],
        [Name],
        [Description],
        [CostCodeId],
        [Aliases]
    FROM dbo.[SubCostCode];
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
    @Aliases NVARCHAR(500) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[SubCostCode] ([CreatedDatetime], [ModifiedDatetime], [Number], [Name], [Description], [CostCodeId], [Aliases])
    VALUES (@Now, @Now, @Number, @Name, @Description, @CostCodeId, @Aliases);

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

CREATE OR ALTER PROCEDURE FindSubCostCodeForReply
(
    @Hint NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @HintNorm NVARCHAR(255) = LTRIM(RTRIM(ISNULL(@Hint, '')));

    -- Segment-pad helper: split @HintNorm on '.', left-pad each segment
    -- to 2 chars with '0'. "13.1" → "13.01"; "5.2" → "05.02"; "13.01"
    -- stays "13.01". Limited to single dot (the convention).
    DECLARE @PaddedHint NVARCHAR(255) = NULL;
    IF @HintNorm <> '' AND CHARINDEX('.', @HintNorm) > 0
    BEGIN
        DECLARE @LeftPart NVARCHAR(50) = LEFT(@HintNorm, CHARINDEX('.', @HintNorm) - 1);
        DECLARE @RightPart NVARCHAR(50) = SUBSTRING(@HintNorm, CHARINDEX('.', @HintNorm) + 1, 250);
        IF LEN(@LeftPart) = 1 SET @LeftPart = '0' + @LeftPart;
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
