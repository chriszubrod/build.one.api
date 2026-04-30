-- Phase 0 — Access Control Rebuild
-- Adds CompanyId (per-Company role assignment) + audit attribution to
-- dbo.UserRole. Drops the old (UserId,RoleId) unique; adds the new
-- (UserId,CompanyId,RoleId) unique. CompanyId stays NULL in Phase 0;
-- Phase 1 backfill fills it then flips to NOT NULL.
-- Idempotent.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CompanyId' AND object_id = OBJECT_ID('dbo.UserRole'))
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD [CompanyId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_Company')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CreatedByUserId' AND object_id = OBJECT_ID('dbo.UserRole'))
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD [CreatedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'ModifiedByUserId' AND object_id = OBJECT_ID('dbo.UserRole'))
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD [ModifiedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- Index for filtering UserRole rows by active Company.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_UserRole_UserId_CompanyId' AND object_id = OBJECT_ID('dbo.UserRole'))
BEGIN
    CREATE INDEX [IX_UserRole_UserId_CompanyId] ON [dbo].[UserRole] ([UserId], [CompanyId]);
END
GO

-- Drop the old (UserId, RoleId) unique constraint. The new uniqueness
-- key is (UserId, CompanyId, RoleId) — same user can hold the same role
-- across multiple Companies. The new unique is added in Phase 1 once
-- CompanyId is fully backfilled.
IF EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_UserRole_UserId_RoleId' AND parent_object_id = OBJECT_ID('dbo.UserRole'))
BEGIN
    ALTER TABLE [dbo].[UserRole] DROP CONSTRAINT [UQ_UserRole_UserId_RoleId];
END
GO
