-- =============================================================================
-- dbo.Contract — canonical single source of truth (U-188)
-- =============================================================================
-- MINIMAL BY DESIGN — BuildersFeeRate is the only business field. The full
-- contract model (contract value, change orders, retainage, dates, and the
-- relationship to the existing Budget entity) is DEFERRED to a formal design
-- conversation. Do not add business columns here without that decision.
--
-- Owner-directed home for a Project's Builder's-Fee rate — the DECIMAL(9,6)
-- fraction the U6 cover page reads (0.100000 = 10%).
--
-- This file is the SOLE home of every Contract sproc (CREATE OR ALTER, re-runnable).
-- The migration scripts/migrations/contract_entity.sql carries ONLY the idempotent
-- table + FK + index DDL — never a sproc body — so single-source stays clean.
--
-- Build order: dbo.User and dbo.Project must exist first (FK targets). See README.md.
-- =============================================================================

IF OBJECT_ID('dbo.Contract', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Contract]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CreatedByUserId] BIGINT NOT NULL DEFAULT 17,
    [ProjectId] BIGINT NOT NULL,
    [BuildersFeeRate] DECIMAL(9,6) NULL   -- fraction: 0.100000 = 10%
);
END
GO


CREATE OR ALTER PROCEDURE CreateContract
(
    @ProjectId BIGINT,
    @BuildersFeeRate DECIMAL(9,6) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Contract]
        ([CreatedDatetime], [ModifiedDatetime], [CreatedByUserId], [ProjectId], [BuildersFeeRate])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[ProjectId],
        INSERTED.[BuildersFeeRate]
    VALUES
        (@Now, @Now, COALESCE(@CreatedByUserId, 17), @ProjectId, @BuildersFeeRate);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadContractByPublicId
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
        [CreatedByUserId],
        [ProjectId],
        [BuildersFeeRate]
    FROM dbo.[Contract]
    WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadContractsByProjectId
(
    @ProjectId BIGINT
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
        [CreatedByUserId],
        [ProjectId],
        [BuildersFeeRate]
    FROM dbo.[Contract]
    WHERE [ProjectId] = @ProjectId
    ORDER BY [Id] ASC;
END;
GO


-- ROWVERSION-guarded; BuildersFeeRate CASE-WHEN preserved (passing NULL keeps the
-- stored value — CLAUDE.md NULL-handling rule). NEVER ROLLBACK in-sproc (pyodbc
-- autocommit-off): a stale token matches no row and returns an empty set.
CREATE OR ALTER PROCEDURE UpdateContractByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @BuildersFeeRate DECIMAL(9,6) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Contract]
    SET
        [ModifiedDatetime] = @Now,
        [BuildersFeeRate]  = CASE WHEN @BuildersFeeRate IS NULL THEN [BuildersFeeRate] ELSE @BuildersFeeRate END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[ProjectId],
        INSERTED.[BuildersFeeRate]
    WHERE [PublicId] = @PublicId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- FK constraints (parents dbo.Project + dbo.User must exist first)
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Contract_Project')
BEGIN
    ALTER TABLE [dbo].[Contract] ADD CONSTRAINT [FK_Contract_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Contract_User')
BEGIN
    ALTER TABLE [dbo].[Contract] ADD CONSTRAINT [FK_Contract_User] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- Lookup indexes
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Contract_ProjectId' AND object_id = OBJECT_ID('dbo.Contract'))
BEGIN
    CREATE INDEX [IX_Contract_ProjectId] ON [dbo].[Contract] ([ProjectId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Contract_PublicId' AND object_id = OBJECT_ID('dbo.Contract'))
BEGIN
    CREATE INDEX [IX_Contract_PublicId] ON [dbo].[Contract] ([PublicId]);
END
GO
