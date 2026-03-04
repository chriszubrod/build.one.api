-- =============================================================================
-- ClassificationOverride
-- =============================================================================
-- Per-sender overrides for the email classifier.  When an override exists for
-- a given email address or domain, the classifier returns the configured type
-- with 100% confidence, bypassing heuristic scoring.
-- =============================================================================

-- ── Table ────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.ClassificationOverride', 'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[ClassificationOverride]
    (
        [Id]               BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId]         UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion]       ROWVERSION NOT NULL,
        [CreatedDatetime]  DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        [ModifiedDatetime] DATETIME2(3) NULL,

        -- Match pattern
        [MatchType]          NVARCHAR(20)  NOT NULL,  -- 'email' | 'domain'
        [MatchValue]         NVARCHAR(320) NOT NULL,  -- 'ar@acme.com' or 'acme.com'

        -- Target classification
        [ClassificationType] NVARCHAR(50) NOT NULL,   -- bill | expense | vendor_credit | inquiry | statement

        -- Metadata
        [Notes]              NVARCHAR(500) NULL,
        [IsActive]           BIT NOT NULL DEFAULT 1,
        [CreatedBy]          NVARCHAR(200) NULL,

        CONSTRAINT [UQ_ClassificationOverride_PublicId] UNIQUE ([PublicId]),
        CONSTRAINT [UQ_ClassificationOverride_Match]    UNIQUE ([MatchType], [MatchValue]),
        CONSTRAINT [CK_ClassificationOverride_MatchType] CHECK ([MatchType] IN ('email', 'domain'))
    );
END
GO

-- ── Indexes ──────────────────────────────────────────────────────────────────

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ClassificationOverride_Active')
BEGIN
    CREATE INDEX [IX_ClassificationOverride_Active]
        ON [dbo].[ClassificationOverride] ([IsActive], [MatchType], [MatchValue]);
END
GO


-- ── CreateClassificationOverride ─────────────────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[CreateClassificationOverride]
    @MatchType          NVARCHAR(20),
    @MatchValue         NVARCHAR(320),
    @ClassificationType NVARCHAR(50),
    @Notes              NVARCHAR(500) = NULL,
    @IsActive           BIT = 1,
    @CreatedBy          NVARCHAR(200) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO [dbo].[ClassificationOverride]
        ([MatchType], [MatchValue], [ClassificationType], [Notes], [IsActive], [CreatedBy])
    VALUES
        (@MatchType, LOWER(LTRIM(RTRIM(@MatchValue))), @ClassificationType, @Notes, @IsActive, @CreatedBy);

    SELECT
        [Id],
        CAST([PublicId] AS NVARCHAR(36)) AS PublicId,
        [RowVersion],
        CONVERT(NVARCHAR(23), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(NVARCHAR(23), [ModifiedDatetime], 126) AS ModifiedDatetime,
        [MatchType],
        [MatchValue],
        [ClassificationType],
        [Notes],
        [IsActive],
        [CreatedBy]
    FROM [dbo].[ClassificationOverride]
    WHERE [Id] = SCOPE_IDENTITY();
END
GO


-- ── ReadClassificationOverrides (list all) ───────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[ReadClassificationOverrides]
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        CAST([PublicId] AS NVARCHAR(36)) AS PublicId,
        [RowVersion],
        CONVERT(NVARCHAR(23), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(NVARCHAR(23), [ModifiedDatetime], 126) AS ModifiedDatetime,
        [MatchType],
        [MatchValue],
        [ClassificationType],
        [Notes],
        [IsActive],
        [CreatedBy]
    FROM [dbo].[ClassificationOverride]
    ORDER BY [IsActive] DESC, [MatchType], [MatchValue];
END
GO


-- ── ReadClassificationOverrideByPublicId ─────────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[ReadClassificationOverrideByPublicId]
    @PublicId NVARCHAR(36)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        CAST([PublicId] AS NVARCHAR(36)) AS PublicId,
        [RowVersion],
        CONVERT(NVARCHAR(23), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(NVARCHAR(23), [ModifiedDatetime], 126) AS ModifiedDatetime,
        [MatchType],
        [MatchValue],
        [ClassificationType],
        [Notes],
        [IsActive],
        [CreatedBy]
    FROM [dbo].[ClassificationOverride]
    WHERE [PublicId] = @PublicId;
END
GO


-- ── UpdateClassificationOverride ─────────────────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[UpdateClassificationOverride]
    @PublicId            NVARCHAR(36),
    @RowVersion          BINARY(8),
    @MatchType           NVARCHAR(20),
    @MatchValue          NVARCHAR(320),
    @ClassificationType  NVARCHAR(50),
    @Notes               NVARCHAR(500) = NULL,
    @IsActive            BIT = 1
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [dbo].[ClassificationOverride]
    SET
        [ModifiedDatetime]  = SYSUTCDATETIME(),
        [MatchType]         = @MatchType,
        [MatchValue]        = LOWER(LTRIM(RTRIM(@MatchValue))),
        [ClassificationType]= @ClassificationType,
        [Notes]             = @Notes,
        [IsActive]          = @IsActive
    WHERE [PublicId] = @PublicId
      AND [RowVersion] = @RowVersion;

    IF @@ROWCOUNT = 0
    BEGIN
        RAISERROR('Concurrency conflict: the record was modified by another user.', 16, 1);
        RETURN;
    END

    SELECT
        [Id],
        CAST([PublicId] AS NVARCHAR(36)) AS PublicId,
        [RowVersion],
        CONVERT(NVARCHAR(23), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(NVARCHAR(23), [ModifiedDatetime], 126) AS ModifiedDatetime,
        [MatchType],
        [MatchValue],
        [ClassificationType],
        [Notes],
        [IsActive],
        [CreatedBy]
    FROM [dbo].[ClassificationOverride]
    WHERE [PublicId] = @PublicId;
END
GO


-- ── DeleteClassificationOverrideByPublicId ───────────────────────────────────

CREATE OR ALTER PROCEDURE [dbo].[DeleteClassificationOverrideByPublicId]
    @PublicId NVARCHAR(36)
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM [dbo].[ClassificationOverride]
    WHERE [PublicId] = @PublicId;

    SELECT @@ROWCOUNT AS DeletedCount;
END
GO


-- ── FindClassificationOverride ───────────────────────────────────────────────
-- Given an email address, check for an exact email match first, then
-- fall back to a domain match.  Returns at most one row.

CREATE OR ALTER PROCEDURE [dbo].[FindClassificationOverride]
    @Email NVARCHAR(320)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @LowerEmail NVARCHAR(320) = LOWER(LTRIM(RTRIM(@Email)));
    DECLARE @Domain     NVARCHAR(320) = RIGHT(@LowerEmail, LEN(@LowerEmail) - CHARINDEX('@', @LowerEmail));

    SELECT TOP 1
        [Id],
        CAST([PublicId] AS NVARCHAR(36)) AS PublicId,
        [RowVersion],
        CONVERT(NVARCHAR(23), [CreatedDatetime],  126) AS CreatedDatetime,
        CONVERT(NVARCHAR(23), [ModifiedDatetime], 126) AS ModifiedDatetime,
        [MatchType],
        [MatchValue],
        [ClassificationType],
        [Notes],
        [IsActive],
        [CreatedBy]
    FROM [dbo].[ClassificationOverride]
    WHERE [IsActive] = 1
      AND (([MatchType] = 'email'  AND [MatchValue] = @LowerEmail)
       OR  ([MatchType] = 'domain' AND [MatchValue] = @Domain))
    ORDER BY
        CASE [MatchType] WHEN 'email' THEN 0 ELSE 1 END;  -- exact match first
END
GO
