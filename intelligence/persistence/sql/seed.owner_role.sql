-- Configure the "Owner" role to mirror Tenant Admin: full CRUD +
-- submit/approve/complete on every module.
--
-- Idempotent — safe to re-run. Uses MERGE so existing Owner grants are
-- updated in place; new modules added later will need a re-run.
--
-- Rationale: Owner is the principal/founder role. The role is currently
-- under-configured (only read on a handful of modules + full Time
-- Tracking), which surfaces permission walls for Austin Rogers and would
-- block any future Owner. This brings it to "unrestricted operator"
-- parity with Tenant Admin, which matches how Owner is used in practice.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.owner_role.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @OwnerRoleId       BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Owner');
DECLARE @TenantAdminRoleId BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Tenant Admin');

IF @OwnerRoleId IS NULL OR @TenantAdminRoleId IS NULL
BEGIN
    RAISERROR('Owner or Tenant Admin role missing.', 16, 1);
    RETURN;
END;

MERGE dbo.RoleModule AS target
USING (
    SELECT @OwnerRoleId AS RoleId, ta.ModuleId,
           ta.CanCreate, ta.CanRead, ta.CanUpdate, ta.CanDelete,
           ta.CanSubmit, ta.CanApprove, ta.CanComplete
    FROM dbo.RoleModule ta
    WHERE ta.RoleId = @TenantAdminRoleId
) AS src
ON target.RoleId = src.RoleId AND target.ModuleId = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET
        CanCreate   = src.CanCreate,
        CanRead     = src.CanRead,
        CanUpdate   = src.CanUpdate,
        CanDelete   = src.CanDelete,
        CanSubmit   = src.CanSubmit,
        CanApprove  = src.CanApprove,
        CanComplete = src.CanComplete,
        ModifiedDatetime = @Now
WHEN NOT MATCHED THEN
    INSERT (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
            CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            src.CanCreate, src.CanRead, src.CanUpdate, src.CanDelete,
            src.CanSubmit, src.CanApprove, src.CanComplete);

DECLARE @Synced INT = @@ROWCOUNT;
PRINT CONCAT('  Owner role: ', @Synced, ' module grant rows synced from Tenant Admin');

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Owner role grants merged from Tenant Admin.';
PRINT '────────────────────────────────────────────────────────────';
