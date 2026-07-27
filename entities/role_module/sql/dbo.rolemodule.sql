IF OBJECT_ID('dbo.RoleModule', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[RoleModule]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [RoleId] BIGINT NOT NULL,
    [ModuleId] BIGINT NOT NULL,
    [CanCreate] BIT NOT NULL DEFAULT 0,
    [CanRead] BIT NOT NULL DEFAULT 0,
    [CanUpdate] BIT NOT NULL DEFAULT 0,
    [CanDelete] BIT NOT NULL DEFAULT 0,
    [CanSubmit] BIT NOT NULL DEFAULT 0,
    [CanApprove] BIT NOT NULL DEFAULT 0,
    [CanComplete] BIT NOT NULL DEFAULT 0,
    [CanViewTeam] BIT NOT NULL CONSTRAINT DF_RoleModule_CanViewTeam DEFAULT (0)
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanViewTeam')
BEGIN
    ALTER TABLE dbo.RoleModule ADD CanViewTeam BIT NOT NULL CONSTRAINT DF_RoleModule_CanViewTeam DEFAULT (0);
END;
GO


GO


GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModules
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule];
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [Id] = @Id;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [PublicId] = @PublicId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByRoleId
(
    @RoleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [RoleId] = @RoleId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByModuleId
(
    @ModuleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [ModuleId] = @ModuleId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.CreateRoleModule
(
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0,
    @CanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2 = SYSUTCDATETIME();
    INSERT INTO dbo.[RoleModule] (
        [CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete], [CanViewTeam]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RoleId], INSERTED.[ModuleId],
        INSERTED.[CanCreate], INSERTED.[CanRead], INSERTED.[CanUpdate], INSERTED.[CanDelete],
        INSERTED.[CanSubmit], INSERTED.[CanApprove], INSERTED.[CanComplete],
        INSERTED.[CanViewTeam]
    VALUES (
        @Now, @Now, @RoleId, @ModuleId,
        @CanCreate, @CanRead, @CanUpdate, @CanDelete,
        @CanSubmit, @CanApprove, @CanComplete, @CanViewTeam
    );
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.UpdateRoleModuleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0,
    @CanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;
    UPDATE dbo.[RoleModule]
       SET [ModifiedDatetime] = SYSUTCDATETIME(),
           [RoleId]      = @RoleId,
           [ModuleId]    = @ModuleId,
           [CanCreate]   = @CanCreate,
           [CanRead]     = @CanRead,
           [CanUpdate]   = @CanUpdate,
           [CanDelete]   = @CanDelete,
           [CanSubmit]   = @CanSubmit,
           [CanApprove]  = @CanApprove,
           [CanComplete] = @CanComplete,
           [CanViewTeam] = @CanViewTeam
        OUTPUT
            INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
            CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
            INSERTED.[RoleId], INSERTED.[ModuleId],
            INSERTED.[CanCreate], INSERTED.[CanRead], INSERTED.[CanUpdate], INSERTED.[CanDelete],
            INSERTED.[CanSubmit], INSERTED.[CanApprove], INSERTED.[CanComplete],
            INSERTED.[CanViewTeam]
     WHERE [Id] = @Id
       AND [RowVersion] = @RowVersion;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteRoleModuleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[RoleModule]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[RoleId],
        DELETED.[ModuleId],
        DELETED.[CanCreate],
        DELETED.[CanRead],
        DELETED.[CanUpdate],
        DELETED.[CanDelete],
        DELETED.[CanSubmit],
        DELETED.[CanApprove],
        DELETED.[CanComplete]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- ── Constraints — canonical declarations ─────────────────────────────────────
-- These are the schema this table is DECLARED to have; a from-scratch build (see
-- sql/README.md) creates them from this file. They were absent from prod until
-- U-053: the missing GO above swallowed the first block into
-- DeleteRoleModuleById's body (fixed in U-048) and the base was never re-applied.
-- U-053 applies all three deliberately via
--   entities/role_module/sql/migrations/001_rbac_join_integrity_constraints.sql
-- (data-guarded + self-verifying) rather than as an untracked side effect of a
-- base re-apply. Keep them here — the base is the declared schema, the migration
-- is the apply vehicle, exactly as sprocs live here and are applied by running
-- this file.
--
-- APPLY STATUS: the migration is committed but the prod apply is a separately
-- gated action. Until it has run, prod still has NO foreign keys and no unique
-- constraint on this table. Confirm with the read-back query in TODO.md before
-- assuming base == live for the constraints (the 8 sprocs are already base == live).
-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_RoleModule_Role')
BEGIN
    ALTER TABLE [dbo].[RoleModule] ADD CONSTRAINT [FK_RoleModule_Role] FOREIGN KEY ([RoleId]) REFERENCES [dbo].[Role]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_RoleModule_Module')
BEGIN
    ALTER TABLE [dbo].[RoleModule] ADD CONSTRAINT [FK_RoleModule_Module] FOREIGN KEY ([ModuleId]) REFERENCES [dbo].[Module]([Id]);
END
GO

-- Prevent duplicate role-module assignments
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_RoleModule_RoleId_ModuleId' AND parent_object_id = OBJECT_ID('dbo.RoleModule'))
BEGIN
    ALTER TABLE [dbo].[RoleModule] ADD CONSTRAINT [UQ_RoleModule_RoleId_ModuleId] UNIQUE ([RoleId], [ModuleId]);
END
GO
