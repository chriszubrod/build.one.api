-- Phase 0 — Access Control Rebuild
-- Adds multi-user attribution columns to dbo.Organization. Idempotent.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CreatedByUserId' AND object_id = OBJECT_ID('dbo.Organization'))
BEGIN
    ALTER TABLE [dbo].[Organization] ADD [CreatedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'ModifiedByUserId' AND object_id = OBJECT_ID('dbo.Organization'))
BEGIN
    ALTER TABLE [dbo].[Organization] ADD [ModifiedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Organization_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Organization] ADD CONSTRAINT [FK_Organization_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Organization_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[Organization] ADD CONSTRAINT [FK_Organization_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Organization_CreatedByUserId' AND object_id = OBJECT_ID('dbo.Organization'))
BEGIN
    CREATE INDEX [IX_Organization_CreatedByUserId] ON [dbo].[Organization] ([CreatedByUserId]);
END
GO
