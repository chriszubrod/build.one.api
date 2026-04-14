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
