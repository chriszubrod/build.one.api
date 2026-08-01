-- =============================================================================
-- Migration: Contract entity (U-188)
-- =============================================================================
-- MINIMAL BY DESIGN — BuildersFeeRate is the only business field. The full
-- contract model (contract value, change orders, retainage, dates, and the
-- relationship to the existing Budget entity) is DEFERRED to a formal design
-- conversation.
--
-- Idempotent table + FK + index DDL for dbo.Contract. Sprocs are NOT defined here
-- — they live ONLY in entities/contract/sql/dbo.contract.sql (single source of
-- truth). Re-runnable safely; apply after dbo.User and dbo.Project exist.
--   python scripts/run_sql.py scripts/migrations/contract_entity.sql
-- Then apply the canonical base file for the sprocs:
--   python scripts/run_sql.py entities/contract/sql/dbo.contract.sql
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

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Contract_Project')
BEGIN
    ALTER TABLE [dbo].[Contract] ADD CONSTRAINT [FK_Contract_Project]
        FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Contract_User')
BEGIN
    ALTER TABLE [dbo].[Contract] ADD CONSTRAINT [FK_Contract_User]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

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
