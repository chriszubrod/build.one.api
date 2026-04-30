-- Phase 0 — Access Control Rebuild
-- Adds multi-user attribution columns to dbo.UserProject. Idempotent.
-- (No CompanyId — Project already carries CompanyId, so UserProject
-- inherits it transitively.)

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CreatedByUserId' AND object_id = OBJECT_ID('dbo.UserProject'))
BEGIN
    ALTER TABLE [dbo].[UserProject] ADD [CreatedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'ModifiedByUserId' AND object_id = OBJECT_ID('dbo.UserProject'))
BEGIN
    ALTER TABLE [dbo].[UserProject] ADD [ModifiedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserProject_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[UserProject] ADD CONSTRAINT [FK_UserProject_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserProject_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[UserProject] ADD CONSTRAINT [FK_UserProject_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO
