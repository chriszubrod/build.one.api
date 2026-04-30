-- Phase 0 — Access Control Rebuild
-- Adds CompanyId (per-Company additive grant) + audit attribution to
-- dbo.UserModule. Drops the old (UserId, ModuleId) unique; the new
-- (UserId, CompanyId, ModuleId) unique lands in Phase 1 once CompanyId
-- is fully backfilled.
-- Idempotent.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CompanyId' AND object_id = OBJECT_ID('dbo.UserModule'))
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD [CompanyId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserModule_Company')
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD CONSTRAINT [FK_UserModule_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CreatedByUserId' AND object_id = OBJECT_ID('dbo.UserModule'))
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD [CreatedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'ModifiedByUserId' AND object_id = OBJECT_ID('dbo.UserModule'))
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD [ModifiedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserModule_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD CONSTRAINT [FK_UserModule_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserModule_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD CONSTRAINT [FK_UserModule_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_UserModule_UserId_CompanyId' AND object_id = OBJECT_ID('dbo.UserModule'))
BEGIN
    CREATE INDEX [IX_UserModule_UserId_CompanyId] ON [dbo].[UserModule] ([UserId], [CompanyId]);
END
GO

-- Drop the old (UserId, ModuleId) unique. New unique with CompanyId
-- lands in Phase 1.
IF EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_UserModule_UserId_ModuleId' AND parent_object_id = OBJECT_ID('dbo.UserModule'))
BEGIN
    ALTER TABLE [dbo].[UserModule] DROP CONSTRAINT [UQ_UserModule_UserId_ModuleId];
END
GO
