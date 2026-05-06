GO




IF OBJECT_ID('dbo.Project', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Project]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Description] NVARCHAR(500) NULL,
    [Status] NVARCHAR(50) NULL,
    [CustomerId] BIGINT NULL,
    [Abbreviation] NVARCHAR(20) NULL,
    CONSTRAINT [FK_Project_Customer] FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customer]([Id])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateProject
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(500),
    @Status NVARCHAR(50),
    @CustomerId BIGINT NULL,
    @Abbreviation NVARCHAR(20) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Project] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [Status], [CustomerId], [Abbreviation])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[Status],
        INSERTED.[CustomerId],
        INSERTED.[Abbreviation]
    VALUES (@Now, @Now, @Name, @Description, @Status, @CustomerId, @Abbreviation);

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadProjects
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadProjectById
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadProjectByPublicId
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadProjectByName
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
        [Description],
        [Status],
        [CustomerId],
        [Abbreviation]
    FROM dbo.[Project]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateProjectById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Description NVARCHAR(500),
    @Status NVARCHAR(50),
    @CustomerId BIGINT NULL,
    @Abbreviation NVARCHAR(20) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Project]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [Status] = @Status,
        [CustomerId] = CASE WHEN @CustomerId IS NULL THEN [CustomerId] ELSE @CustomerId END,
        [Abbreviation] = @Abbreviation
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[Status],
        INSERTED.[CustomerId],
        INSERTED.[Abbreviation]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteProjectById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Project]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[Status],
        DELETED.[CustomerId],
        DELETED.[Abbreviation]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Project' AND COLUMN_NAME = 'Abbreviation'
)
BEGIN
    ALTER TABLE dbo.Project
    ADD Abbreviation NVARCHAR(20) NULL;
END
GO


-- User-scoped read: returns Project records the user has access to,
-- joined through dbo.UserProject. Used by iOS so the client doesn't
-- have to fetch all projects and filter client-side.
CREATE OR ALTER PROCEDURE ReadProjectsByUserId
(
    @UserId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT DISTINCT
        p.[Id],
        p.[PublicId],
        p.[RowVersion],
        CONVERT(VARCHAR(19), p.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), p.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        p.[Name],
        p.[Description],
        p.[Status],
        p.[CustomerId],
        p.[Abbreviation]
    FROM dbo.[Project] p
    INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = p.[Id]
    WHERE up.[UserId] = @UserId
    ORDER BY p.[Name] ASC;

    COMMIT TRANSACTION;
END;
GO

-- ============================================================================
-- FindProjectForInvoice — single-call ranked Project lookup for invoice
-- classification. Mirrors FindVendorForInvoice. Used by the
-- project_specialist agent (delegated from bill_specialist) when an
-- invoice's "Ship To" / job-site address needs to be bound to an
-- existing Project row.
--
-- Project.Name typically encodes the address (e.g. "TB3 - 917 Tyne Blvd",
-- "HA - 206 Haverford Ave"), so substring-on-Name catches most
-- invoice-driven lookups. The agent is responsible for cleaning the
-- raw address into something searchable (strip phone numbers, repeated
-- lines, city/state/zip) before calling.
--
-- Strategies (descending confidence):
--   0.95  exact_name              — Project.Name == @ProjectNameHint (case-insensitive)
--   0.90  exact_abbreviation      — Project.Abbreviation == @ProjectNameHint
--   0.85  substring_address_full  — Project.Name CONTAINS @AddressHint (full)
--   0.75  substring_address_part  — Project.Name CONTAINS first 2 tokens of @AddressHint
--   0.65  substring_first_token   — Project.Name CONTAINS first token (often the street number)
--
-- Each Project row appears at most once at its highest-scoring strategy.
-- Returns up to 5 candidates ordered by confidence desc.
-- ============================================================================

CREATE OR ALTER PROCEDURE FindProjectForInvoice
(
    @AddressHint NVARCHAR(500) = NULL,
    @ProjectNameHint NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @AddressNorm NVARCHAR(500) = LTRIM(RTRIM(ISNULL(@AddressHint, '')));
    DECLARE @NameNorm NVARCHAR(255) = LTRIM(RTRIM(ISNULL(@ProjectNameHint, '')));

    -- Extract first token and first 2 tokens of the address. For
    -- "917 TYNE BLVD" → first token "917", first two tokens "917 TYNE".
    DECLARE @FirstSpace INT = NULLIF(CHARINDEX(' ', @AddressNorm), 0);
    DECLARE @SecondSpace INT = NULLIF(CHARINDEX(' ', @AddressNorm, ISNULL(@FirstSpace, 0) + 1), 0);

    DECLARE @FirstToken NVARCHAR(100) =
        CASE WHEN @FirstSpace IS NOT NULL THEN LEFT(@AddressNorm, @FirstSpace - 1)
             ELSE @AddressNorm END;
    DECLARE @TwoTokens NVARCHAR(255) =
        CASE WHEN @SecondSpace IS NOT NULL THEN LEFT(@AddressNorm, @SecondSpace - 1)
             ELSE @AddressNorm END;

    -- LIKE escapes
    DECLARE @AddressLike   NVARCHAR(502) = REPLACE(REPLACE(LOWER(@AddressNorm),  '%', '[%]'), '_', '[_]');
    DECLARE @TwoTokensLike NVARCHAR(257) = REPLACE(REPLACE(LOWER(@TwoTokens),    '%', '[%]'), '_', '[_]');
    DECLARE @FirstTokenLike NVARCHAR(102) = REPLACE(REPLACE(LOWER(@FirstToken),  '%', '[%]'), '_', '[_]');
    DECLARE @NameLike      NVARCHAR(257) = REPLACE(REPLACE(LOWER(@NameNorm),     '%', '[%]'), '_', '[_]');

    ;WITH
    exact_name AS (
        SELECT [Id], CAST(0.95 AS DECIMAL(3,2)) AS Confidence,
               CAST('exact_name' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Project]
        WHERE @NameNorm <> '' AND LOWER([Name]) = LOWER(@NameNorm)
    ),
    exact_abbr AS (
        SELECT [Id], CAST(0.90 AS DECIMAL(3,2)) AS Confidence,
               CAST('exact_abbreviation' AS NVARCHAR(50)) AS Strategy,
               [Abbreviation] AS MatchedTerm
        FROM dbo.[Project]
        WHERE @NameNorm <> '' AND [Abbreviation] IS NOT NULL
          AND LOWER([Abbreviation]) = LOWER(@NameNorm)
    ),
    substring_address_full AS (
        SELECT [Id], CAST(0.85 AS DECIMAL(3,2)) AS Confidence,
               CAST('substring_address_full' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Project]
        WHERE @AddressNorm <> ''
          AND LOWER([Name]) LIKE '%' + @AddressLike + '%'
    ),
    substring_address_part AS (
        SELECT [Id], CAST(0.75 AS DECIMAL(3,2)) AS Confidence,
               CAST('substring_address_part' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Project]
        WHERE @TwoTokens <> ''
          AND LOWER([Name]) LIKE '%' + @TwoTokensLike + '%'
    ),
    substring_first_token AS (
        -- Often this is the street number; high precision when the
        -- street number is unique across the project portfolio.
        SELECT [Id], CAST(0.65 AS DECIMAL(3,2)) AS Confidence,
               CAST('substring_first_token' AS NVARCHAR(50)) AS Strategy,
               [Name] AS MatchedTerm
        FROM dbo.[Project]
        WHERE @FirstToken <> ''
          AND LOWER([Name]) LIKE '%' + @FirstTokenLike + '%'
    ),
    all_candidates AS (
        SELECT * FROM exact_name
        UNION ALL SELECT * FROM exact_abbr
        UNION ALL SELECT * FROM substring_address_full
        UNION ALL SELECT * FROM substring_address_part
        UNION ALL SELECT * FROM substring_first_token
    ),
    ranked AS (
        SELECT [Id], Confidence, Strategy, MatchedTerm,
               ROW_NUMBER() OVER (PARTITION BY [Id] ORDER BY Confidence DESC) AS rn
        FROM all_candidates
    )
    SELECT TOP 5
        p.[Id]                              AS ProjectId,
        CAST(p.[PublicId] AS NVARCHAR(36))  AS ProjectPublicId,
        p.[Name]                            AS ProjectName,
        p.[Abbreviation]                    AS Abbreviation,
        p.[Status]                          AS Status,
        r.Confidence                        AS Confidence,
        r.Strategy                          AS Strategy,
        r.MatchedTerm                       AS MatchedTerm
    FROM ranked r
    INNER JOIN dbo.[Project] p ON p.[Id] = r.[Id]
    WHERE r.rn = 1
    ORDER BY r.Confidence DESC, p.[Name] ASC;
END;
