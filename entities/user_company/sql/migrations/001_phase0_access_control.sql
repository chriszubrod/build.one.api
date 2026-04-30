-- Phase 0 — Access Control Rebuild
-- Adds multi-user attribution columns to dbo.UserCompany. Idempotent.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CreatedByUserId' AND object_id = OBJECT_ID('dbo.UserCompany'))
BEGIN
    ALTER TABLE [dbo].[UserCompany] ADD [CreatedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'ModifiedByUserId' AND object_id = OBJECT_ID('dbo.UserCompany'))
BEGIN
    ALTER TABLE [dbo].[UserCompany] ADD [ModifiedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserCompany_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[UserCompany] ADD CONSTRAINT [FK_UserCompany_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserCompany_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[UserCompany] ADD CONSTRAINT [FK_UserCompany_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO
