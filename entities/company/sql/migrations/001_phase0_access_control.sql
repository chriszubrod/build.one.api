-- Phase 0 — Access Control Rebuild
-- Adds OrganizationId (1:N replacement for OrganizationCompany M:N) +
-- multi-user attribution columns to dbo.Company. OrganizationId stays
-- nullable in Phase 0; Phase 1 backfill sets it then flips to NOT NULL.
-- Idempotent.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'OrganizationId' AND object_id = OBJECT_ID('dbo.Company'))
BEGIN
    ALTER TABLE [dbo].[Company] ADD [OrganizationId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Company_Organization')
BEGIN
    ALTER TABLE [dbo].[Company] ADD CONSTRAINT [FK_Company_Organization]
        FOREIGN KEY ([OrganizationId]) REFERENCES [dbo].[Organization]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Company_OrganizationId' AND object_id = OBJECT_ID('dbo.Company'))
BEGIN
    CREATE INDEX [IX_Company_OrganizationId] ON [dbo].[Company] ([OrganizationId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CreatedByUserId' AND object_id = OBJECT_ID('dbo.Company'))
BEGIN
    ALTER TABLE [dbo].[Company] ADD [CreatedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'ModifiedByUserId' AND object_id = OBJECT_ID('dbo.Company'))
BEGIN
    ALTER TABLE [dbo].[Company] ADD [ModifiedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Company_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Company] ADD CONSTRAINT [FK_Company_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Company_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[Company] ADD CONSTRAINT [FK_Company_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Company_CreatedByUserId' AND object_id = OBJECT_ID('dbo.Company'))
BEGIN
    CREATE INDEX [IX_Company_CreatedByUserId] ON [dbo].[Company] ([CreatedByUserId]);
END
GO
