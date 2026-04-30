-- Phase 0 — Access Control Rebuild
-- Adds IsSystemAdmin / IsAgent / LastCompanyId discriminators + audit
-- attribution columns to dbo.User. All additive; no enforcement yet.
-- Idempotent — safe to re-run.

-- IsSystemAdmin: replaces magic role-name "admin" check. Build.One staff
-- with this flag bypass tenant + module checks system-wide.
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'IsSystemAdmin' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    ALTER TABLE [dbo].[User] ADD [IsSystemAdmin] BIT NOT NULL CONSTRAINT [DF_User_IsSystemAdmin] DEFAULT 0;
END
GO

-- IsAgent: distinguishes LLM agent users from humans for UI filtering.
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'IsAgent' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    ALTER TABLE [dbo].[User] ADD [IsAgent] BIT NOT NULL CONSTRAINT [DF_User_IsAgent] DEFAULT 0;
END
GO

-- LastCompanyId: default `cid` on next login when user has multiple Companies.
-- Updated on every successful switch-company.
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'LastCompanyId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    ALTER TABLE [dbo].[User] ADD [LastCompanyId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_LastCompany')
BEGIN
    ALTER TABLE [dbo].[User] ADD CONSTRAINT [FK_User_LastCompany]
        FOREIGN KEY ([LastCompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

-- Multi-user attribution: who created and last modified the row.
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'CreatedByUserId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    ALTER TABLE [dbo].[User] ADD [CreatedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'ModifiedByUserId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    ALTER TABLE [dbo].[User] ADD [ModifiedByUserId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[User] ADD CONSTRAINT [FK_User_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[User] ADD CONSTRAINT [FK_User_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- Index for "users I created" lookups.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_User_CreatedByUserId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    CREATE INDEX [IX_User_CreatedByUserId] ON [dbo].[User] ([CreatedByUserId]);
END
GO
