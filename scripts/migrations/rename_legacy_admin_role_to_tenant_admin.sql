-- =====================================================================
-- Rename legacy 'Admin' role -> 'Tenant Admin' (2026-05-07).
--
-- Phase 2 (2026-05-06) replaced the role-name "admin" magic-string
-- bypass with `User.IsSystemAdmin = 1`. Phase 2 also seeded a fresh
-- 'Tenant Admin' role (RoleId=15) with no grants.
--
-- The legacy 'Admin' role (RoleId=1) carries 23 fully-populated
-- RoleModule grants (full CRUD across every module) — exactly what
-- 'Tenant Admin' is meant to be. Rather than copy 23 rows + drop the
-- legacy row, we:
--   1. Delete the empty seeded Tenant Admin (RoleId=15, zero refs).
--   2. Rename Role.Id=1 from 'Admin' to 'Tenant Admin'.
--
-- Result: one canonical 'Tenant Admin' role keyed at RoleId=1,
-- preserving its 23 RoleModule grants and the 2 UserRole assignments
-- (Christopher uid=17 + Apple Reviewer uid=32; both IsSystemAdmin=1
-- so the role assignment is dead weight for them — harmless).
--
-- Idempotent — safe to re-run (deletes guarded; rename is no-op once
-- applied).
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

BEGIN TRANSACTION;

-- 1. Delete the empty seeded Tenant Admin row (RoleId=15) ONLY if
--    fully unreferenced. Defensive — pre-flight already confirmed
--    zero refs in UserRole / UserProject / RoleModule.
IF EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = 'Tenant Admin' AND [Id] <> 1)
BEGIN
    DECLARE @SeededTenantAdminId BIGINT;
    SELECT @SeededTenantAdminId = [Id] FROM dbo.[Role] WHERE [Name] = 'Tenant Admin' AND [Id] <> 1;

    IF EXISTS (SELECT 1 FROM dbo.[UserRole] WHERE [RoleId] = @SeededTenantAdminId)
        OR EXISTS (SELECT 1 FROM dbo.[UserProject] WHERE [RoleId] = @SeededTenantAdminId)
        OR EXISTS (SELECT 1 FROM dbo.[RoleModule] WHERE [RoleId] = @SeededTenantAdminId)
    BEGIN
        RAISERROR('Seeded Tenant Admin (Id=%d) has references — aborting rename. Investigate.', 16, 1, @SeededTenantAdminId);
        ROLLBACK TRANSACTION;
        RETURN;
    END

    DELETE FROM dbo.[Role] WHERE [Id] = @SeededTenantAdminId;
    PRINT 'Deleted empty seeded Tenant Admin Role row.';
END
ELSE
BEGIN
    PRINT 'No separate seeded Tenant Admin row to delete (already cleaned up).';
END;

-- 2. Rename Role.Id=1 from 'Admin' to 'Tenant Admin'.
IF EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Id] = 1 AND [Name] = 'Admin')
BEGIN
    UPDATE dbo.[Role] SET [Name] = 'Tenant Admin' WHERE [Id] = 1;
    PRINT 'Renamed Role.Id=1 from Admin to Tenant Admin.';
END
ELSE IF EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Id] = 1 AND [Name] = 'Tenant Admin')
BEGIN
    PRINT 'Role.Id=1 already named Tenant Admin (idempotent re-run).';
END
ELSE
BEGIN
    RAISERROR('Role.Id=1 not found or unexpected name. Investigate before re-running.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

COMMIT TRANSACTION;
GO

PRINT 'Done. Canonical Tenant Admin lives at Role.Id=1 with 23 RoleModule grants.';
GO
