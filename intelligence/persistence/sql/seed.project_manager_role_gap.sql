-- Patch the Project Manager role with two missing module grants needed
-- by the React UI:
--   Projects   read  — for /get/user_projects/user/{id} on the Profile page
--   Dashboard  read  — for /api/v1/lookups (powers most dropdowns)
--
-- Idempotent — safe to re-run.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.project_manager_role_gap.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @PMRoleId   BIGINT = (SELECT Id FROM dbo.Role     WHERE Name = 'Project Manager');
DECLARE @ProjectsId INT    = (SELECT Id FROM dbo.[Module] WHERE Name = 'Projects');
DECLARE @DashboardId INT   = (SELECT Id FROM dbo.[Module] WHERE Name = 'Dashboard');

IF @PMRoleId IS NULL OR @ProjectsId IS NULL OR @DashboardId IS NULL
BEGIN
    RAISERROR('Project Manager role or required Modules missing.', 16, 1);
    RETURN;
END;

DECLARE @Grants TABLE (ModuleId INT);
INSERT INTO @Grants VALUES (@ProjectsId), (@DashboardId);

MERGE dbo.RoleModule AS target
USING (SELECT @PMRoleId AS RoleId, ModuleId FROM @Grants) AS src
ON target.RoleId = src.RoleId AND target.ModuleId = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET CanRead = 1, ModifiedDatetime = @Now
WHEN NOT MATCHED THEN
    INSERT (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
            CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, src.RoleId, src.ModuleId, 0, 1, 0, 0, 0, 0, 0);

PRINT '  Project Manager role: Projects read + Dashboard read merged';
