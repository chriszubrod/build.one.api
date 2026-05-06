-- Phase 1b — Review Notifications
-- Adds a Role qualifier to UserProject so each row records which role
-- the user holds on the project (e.g., 'Project Manager' = To: on
-- notifications; 'Owner' = Cc:). NULL preserves the meaning of existing
-- rows as generic project membership without role qualification.
-- Idempotent.

IF OBJECT_ID('dbo.UserProject', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.UserProject') AND name = 'RoleId')
BEGIN
    ALTER TABLE [dbo].[UserProject] ADD [RoleId] BIGINT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserProject_Role')
BEGIN
    ALTER TABLE [dbo].[UserProject]
    ADD CONSTRAINT [FK_UserProject_Role] FOREIGN KEY ([RoleId]) REFERENCES [dbo].[Role]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_UserProject_RoleId' AND object_id = OBJECT_ID('dbo.UserProject'))
BEGIN
    CREATE INDEX [IX_UserProject_RoleId] ON [dbo].[UserProject] ([RoleId]);
END
GO
