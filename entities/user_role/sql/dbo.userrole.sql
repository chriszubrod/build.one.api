IF OBJECT_ID('dbo.UserRole', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[UserRole]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [UserId] BIGINT NOT NULL,
    [RoleId] BIGINT NOT NULL
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId/ModifiedByUserId param/INSERT-list references below — live since
-- entities/user_role/sql/migrations/001_phase0_access_control.sql. No-op against the live
-- schema (columns/FKs already exist there).
IF OBJECT_ID('dbo.UserRole', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.UserRole') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD [CreatedByUserId] BIGINT NULL;
END
GO
IF OBJECT_ID('dbo.UserRole', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.UserRole') AND name = 'ModifiedByUserId')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD [ModifiedByUserId] BIGINT NULL;
END
GO
IF OBJECT_ID('dbo.UserRole', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO
IF OBJECT_ID('dbo.UserRole', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_ModifiedByUser')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_ModifiedByUser]
        FOREIGN KEY ([ModifiedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

CREATE OR ALTER PROCEDURE CreateUserRole
(
    @UserId BIGINT,
    @RoleId BIGINT,
    @CompanyId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[UserRole]
        ([CreatedDatetime], [ModifiedDatetime], [UserId], [RoleId],
         [CompanyId], [CreatedByUserId], [ModifiedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[RoleId],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    VALUES
        (@Now, @Now, @UserId, @RoleId,
         @CompanyId, @CreatedByUserId, COALESCE(@ModifiedByUserId, @CreatedByUserId));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserRoles
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserRole]
    ORDER BY [UserId] ASC, [RoleId] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserRoleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserRole]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserRoleByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserRole]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserRoleByUserId
(
    @UserId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserRole]
    WHERE [UserId] = @UserId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserRoleByRoleId
(
    @RoleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserRole]
    WHERE [RoleId] = @RoleId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserRolesByUserId
(
    @UserId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserRole]
    WHERE [UserId] = @UserId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateUserRoleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @RoleId BIGINT,
    @CompanyId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[UserRole]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [RoleId] = @RoleId,
        -- Preserve existing CompanyId if caller passes NULL (keeps
        -- pre-Phase-1 update paths working until services catch up).
        [CompanyId] = CASE WHEN @CompanyId IS NULL THEN [CompanyId] ELSE @CompanyId END,
        [ModifiedByUserId] = @ModifiedByUserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[RoleId],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteUserRoleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[UserRole]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[RoleId],
        DELETED.[CompanyId],
        DELETED.[CreatedByUserId],
        DELETED.[ModifiedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_User')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_Role')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_Role] FOREIGN KEY ([RoleId]) REFERENCES [dbo].[Role]([Id]);
END
GO

-- Uniqueness key is (UserId, CompanyId, RoleId) via UQ_UserRole_UserId_CompanyId_RoleId,
-- added by scripts/migrations/phase1_not_null_flip.sql — deliberately NOT re-asserted here;
-- post-001 schema stays in migrations. Never re-add the old 2-col unique.

-- U-126 (2026-07-23): homed from migration 002; body is the LIVE prod definition captured via sys.sql_modules.
-- New: Phase 2 permission resolver fetches the user's roles scoped to
-- the active Company. Returns ALL UserRole rows for the (user, company)
-- pair so the resolver can OR their RoleModule grants together.
CREATE OR ALTER PROCEDURE ReadUserRolesByUserIdAndCompanyId
(
    @UserId BIGINT,
    @CompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId],
        [CompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[UserRole]
    WHERE [UserId] = @UserId AND [CompanyId] = @CompanyId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO
