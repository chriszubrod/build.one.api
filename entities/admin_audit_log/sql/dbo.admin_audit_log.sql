-- AdminAuditLog — durable audit trail for system-admin user-management actions (U-585).
-- Run: ./.venv/bin/python scripts/run_sql.py entities/admin_audit_log/sql/dbo.admin_audit_log.sql

GO

IF OBJECT_ID('dbo.AdminAuditLog', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[AdminAuditLog]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [ActorUserId] BIGINT NULL,
    [ActorIsSystemAdmin] BIT NOT NULL DEFAULT 0,
    [Action] VARCHAR(64) NOT NULL,
    [TargetUserId] BIGINT NULL,
    [Detail] NVARCHAR(MAX) NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AdminAuditLog_TargetUser_Created' AND object_id = OBJECT_ID('dbo.AdminAuditLog'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_AdminAuditLog_TargetUser_Created
        ON dbo.[AdminAuditLog] ([TargetUserId], [CreatedDatetime] DESC);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AdminAuditLog_CreatedDatetime' AND object_id = OBJECT_ID('dbo.AdminAuditLog'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_AdminAuditLog_CreatedDatetime
        ON dbo.[AdminAuditLog] ([CreatedDatetime] DESC);
END
GO

-- DELIBERATELY NO FOREIGN KEYS on ActorUserId / TargetUserId (U-585 Pass-1 P2).
-- This is an append-only audit trail: a row must outlive the users it names.
-- An FK (default action) would make the pre-existing DeleteUserById sproc fail
-- for any user that was ever an admin actor or target; ON DELETE SET NULL would
-- rewrite history (the row would then read as "system acted on nobody"). The
-- ids stay as recorded; readers resolve names best-effort and show "User #id"
-- when the user no longer exists. Drop the FK guard blocks if re-applying an
-- older version of this file that still carried them:
IF OBJECT_ID('dbo.AdminAuditLog', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AdminAuditLog_ActorUser')
BEGIN
    ALTER TABLE [dbo].[AdminAuditLog] DROP CONSTRAINT [FK_AdminAuditLog_ActorUser];
END
GO

IF OBJECT_ID('dbo.AdminAuditLog', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AdminAuditLog_TargetUser')
BEGIN
    ALTER TABLE [dbo].[AdminAuditLog] DROP CONSTRAINT [FK_AdminAuditLog_TargetUser];
END
GO


CREATE OR ALTER PROCEDURE CreateAdminAuditLog
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = 0,
    @Action VARCHAR(64),
    @TargetUserId BIGINT = NULL,
    @Detail NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO dbo.[AdminAuditLog]
        ([ActorUserId], [ActorIsSystemAdmin], [Action], [TargetUserId], [Detail])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        CONVERT(VARCHAR(30), INSERTED.[CreatedDatetime], 126) AS [CreatedDatetime],
        INSERTED.[ActorUserId],
        INSERTED.[ActorIsSystemAdmin],
        INSERTED.[Action],
        INSERTED.[TargetUserId],
        INSERTED.[Detail]
    VALUES
        (@ActorUserId, @ActorIsSystemAdmin, @Action, @TargetUserId, @Detail);
END
GO


CREATE OR ALTER PROCEDURE ReadAdminAuditLogByTargetUserId
(
    @TargetUserId BIGINT,
    @Limit INT = 50
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Limit)
        a.[Id],
        a.[PublicId],
        CONVERT(VARCHAR(30), a.[CreatedDatetime], 126) AS [CreatedDatetime],
        a.[ActorUserId],
        a.[ActorIsSystemAdmin],
        a.[Action],
        a.[TargetUserId],
        a.[Detail]
    FROM dbo.[AdminAuditLog] a
    WHERE a.[TargetUserId] = @TargetUserId
    ORDER BY a.[CreatedDatetime] DESC, a.[Id] DESC;
END
GO


CREATE OR ALTER PROCEDURE ReadAdminAuditLogRecent
(
    @Limit INT = 100
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Limit)
        a.[Id],
        a.[PublicId],
        CONVERT(VARCHAR(30), a.[CreatedDatetime], 126) AS [CreatedDatetime],
        a.[ActorUserId],
        a.[ActorIsSystemAdmin],
        a.[Action],
        a.[TargetUserId],
        a.[Detail]
    FROM dbo.[AdminAuditLog] a
    ORDER BY a.[CreatedDatetime] DESC, a.[Id] DESC;
END
GO
