-- ============================================================================
-- CostCode — Table
-- ============================================================================

IF OBJECT_ID('dbo.CostCode', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.CostCode
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [Number] NVARCHAR(50) NOT NULL,
        [Name] NVARCHAR(255) NOT NULL,
        [Description] NVARCHAR(255) NULL
    );
END;
GO

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_CostCode_PublicId' AND object_id = OBJECT_ID('dbo.CostCode'))
BEGIN
    CREATE INDEX [IX_CostCode_PublicId] ON [dbo].[CostCode] ([PublicId]);
END
GO

-- Idempotent column add for existing environments. Live since migration
-- 238c_qbo_identity_reference.sql; declared here so a fresh environment
-- built from just the base file matches prod (U-289, mirrors U-277's fix
-- for company/address).
IF OBJECT_ID('dbo.CostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.CostCode') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[CostCode] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.CostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.CostCode') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[CostCode] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.CostCode', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_CostCode_QboId_RealmId' AND object_id = OBJECT_ID('dbo.CostCode')
)
BEGIN
    CREATE UNIQUE INDEX UQ_CostCode_QboId_RealmId ON [dbo].[CostCode] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO


-- ============================================================================
-- CostCode — View (single source of truth for column formatting)
-- ============================================================================

GO

CREATE OR ALTER VIEW [dbo].[vw_CostCode]
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
        [QboId],
        [RealmId]
    FROM dbo.[CostCode];
GO


-- ============================================================================
-- CostCode — Stored Procedures
-- ============================================================================

CREATE OR ALTER PROCEDURE CreateCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[CostCode] ([CreatedDatetime], [ModifiedDatetime], [Number], [Name], [Description], [CreatedByUserId])
    VALUES (@Now, @Now, @Number, @Name, @Description, COALESCE(@CreatedByUserId, 17));

    SELECT * FROM dbo.[vw_CostCode] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadCostCodes
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_CostCode] ORDER BY [Number] ASC;
END;
GO


CREATE OR ALTER PROCEDURE ReadCostCodeById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_CostCode] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadCostCodeByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_CostCode] WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadCostCodeByNumber
(
    @Number NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_CostCode] WHERE [Number] = @Number;
END;
GO


CREATE OR ALTER PROCEDURE UpdateCostCodeById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[CostCode]
    SET
        [ModifiedDatetime] = @Now,
        [Number] = @Number,
        [Name] = @Name,
        [Description] = @Description
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    IF @@ROWCOUNT > 0
        SELECT * FROM dbo.[vw_CostCode] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteCostCodeById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT * FROM dbo.[vw_CostCode] WHERE [Id] = @Id;

    DELETE FROM dbo.[CostCode] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- Upsert by Number (for import flows)
CREATE OR ALTER PROCEDURE UpsertCostCode
(
    @Number NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[CostCode] AS target
    USING (SELECT @Number AS Number) AS source
    ON target.[Number] = source.Number

    WHEN MATCHED THEN
        UPDATE SET
            [ModifiedDatetime] = @Now,
            [Name] = @Name,
            [Description] = COALESCE(@Description, target.[Description])

    WHEN NOT MATCHED THEN
        INSERT ([CreatedDatetime], [ModifiedDatetime], [Number], [Name], [Description])
        VALUES (@Now, @Now, @Number, @Name, @Description);

    SELECT * FROM dbo.[vw_CostCode] WHERE [Number] = @Number;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetCostCodeQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50) = NULL,
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[CostCode]
        SET [QboId] = NULL, [RealmId] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[CostCode]
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

-- U-289 (Phase-4 repoint): direct dbo-native identity lookup. Lets the item
-- connector resolve "does a dbo.CostCode already exist for this external QBO
-- item id" WITHOUT hopping through the qbo.ItemCostCode mapping table —
-- every CostCode synced at least once already carries QboId/RealmId via
-- SetCostCodeQboIdentity, so this is the steady-state fast path; the
-- mapping-table lookup remains as a fallback for rows that predate identity
-- stamping. RealmId NULL-equality mirrors SetCostCodeQboIdentity's own
-- stolen-identity comparison.
CREATE OR ALTER PROCEDURE ReadCostCodeByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_CostCode]
    WHERE [QboId] = @QboId
      AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));
END;
GO
