-- Phase 1 — Access Control Rebuild — NOT NULL flips + new uniques
-- Tightens the Phase 0 nullable schema additions now that backfill is
-- done and services pass the new params on every mutation.
-- Idempotent — safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;

-------------------------------------------------------------------------------
-- 1. Company.OrganizationId → NOT NULL
--    Must drop dependent index first, alter, recreate.
-------------------------------------------------------------------------------
IF EXISTS (
    SELECT 1
      FROM sys.columns
     WHERE name = 'OrganizationId'
       AND object_id = OBJECT_ID('dbo.Company')
       AND is_nullable = 1
)
BEGIN
    IF EXISTS (
        SELECT 1 FROM sys.indexes
         WHERE name = 'IX_Company_OrganizationId'
           AND object_id = OBJECT_ID('dbo.Company')
    )
    BEGIN
        DROP INDEX [IX_Company_OrganizationId] ON [dbo].[Company];
    END;

    ALTER TABLE [dbo].[Company]
        ALTER COLUMN [OrganizationId] BIGINT NOT NULL;

    CREATE INDEX [IX_Company_OrganizationId]
        ON [dbo].[Company] ([OrganizationId]);

    PRINT 'Company.OrganizationId flipped to NOT NULL (index recreated).';
END
ELSE
BEGIN
    PRINT 'Company.OrganizationId already NOT NULL — skipping.';
END
GO

-------------------------------------------------------------------------------
-- 2. UserRole.CompanyId → NOT NULL + new (UserId, CompanyId, RoleId) unique
-------------------------------------------------------------------------------
IF EXISTS (
    SELECT 1
      FROM sys.columns
     WHERE name = 'CompanyId'
       AND object_id = OBJECT_ID('dbo.UserRole')
       AND is_nullable = 1
)
BEGIN
    IF EXISTS (
        SELECT 1 FROM sys.indexes
         WHERE name = 'IX_UserRole_UserId_CompanyId'
           AND object_id = OBJECT_ID('dbo.UserRole')
    )
    BEGIN
        DROP INDEX [IX_UserRole_UserId_CompanyId] ON [dbo].[UserRole];
    END;

    ALTER TABLE [dbo].[UserRole]
        ALTER COLUMN [CompanyId] BIGINT NOT NULL;

    CREATE INDEX [IX_UserRole_UserId_CompanyId]
        ON [dbo].[UserRole] ([UserId], [CompanyId]);

    PRINT 'UserRole.CompanyId flipped to NOT NULL (index recreated).';
END
ELSE
BEGIN
    PRINT 'UserRole.CompanyId already NOT NULL — skipping.';
END
GO

IF NOT EXISTS (
    SELECT 1
      FROM sys.objects
     WHERE name = 'UQ_UserRole_UserId_CompanyId_RoleId'
       AND parent_object_id = OBJECT_ID('dbo.UserRole')
)
BEGIN
    ALTER TABLE [dbo].[UserRole]
        ADD CONSTRAINT [UQ_UserRole_UserId_CompanyId_RoleId]
        UNIQUE ([UserId], [CompanyId], [RoleId]);
    PRINT 'UQ_UserRole_UserId_CompanyId_RoleId added.';
END
ELSE
BEGIN
    PRINT 'UQ_UserRole_UserId_CompanyId_RoleId already present — skipping.';
END
GO

-------------------------------------------------------------------------------
-- 3. UserModule.CompanyId → NOT NULL + new (UserId, CompanyId, ModuleId) unique
-------------------------------------------------------------------------------
IF EXISTS (
    SELECT 1
      FROM sys.columns
     WHERE name = 'CompanyId'
       AND object_id = OBJECT_ID('dbo.UserModule')
       AND is_nullable = 1
)
BEGIN
    IF EXISTS (
        SELECT 1 FROM sys.indexes
         WHERE name = 'IX_UserModule_UserId_CompanyId'
           AND object_id = OBJECT_ID('dbo.UserModule')
    )
    BEGIN
        DROP INDEX [IX_UserModule_UserId_CompanyId] ON [dbo].[UserModule];
    END;

    ALTER TABLE [dbo].[UserModule]
        ALTER COLUMN [CompanyId] BIGINT NOT NULL;

    CREATE INDEX [IX_UserModule_UserId_CompanyId]
        ON [dbo].[UserModule] ([UserId], [CompanyId]);

    PRINT 'UserModule.CompanyId flipped to NOT NULL (index recreated).';
END
ELSE
BEGIN
    PRINT 'UserModule.CompanyId already NOT NULL — skipping.';
END
GO

IF NOT EXISTS (
    SELECT 1
      FROM sys.objects
     WHERE name = 'UQ_UserModule_UserId_CompanyId_ModuleId'
       AND parent_object_id = OBJECT_ID('dbo.UserModule')
)
BEGIN
    ALTER TABLE [dbo].[UserModule]
        ADD CONSTRAINT [UQ_UserModule_UserId_CompanyId_ModuleId]
        UNIQUE ([UserId], [CompanyId], [ModuleId]);
    PRINT 'UQ_UserModule_UserId_CompanyId_ModuleId added.';
END
ELSE
BEGIN
    PRINT 'UQ_UserModule_UserId_CompanyId_ModuleId already present — skipping.';
END
GO

PRINT 'Phase 1 NOT NULL flip complete.';
