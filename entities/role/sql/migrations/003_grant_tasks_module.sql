-- Grants on the new 'Tasks' Module (reviewer inbox) for the human roles
-- that should see the worklist on iOS / web.
--
-- Scope assignment:
--   Tenant Admin     — full CRUD (Owner inherits via seed.owner_role.sql merge)
--   Project Manager  — read   (reviewer of bills/expenses on their projects)
--   Reviewer         — read   (explicit reviewer role)
--   AP Specialist    — read   (Sent-box: bills they submitted)
--   AR Specialist    — read   (Sent-box: invoices they submitted)
--
-- 'Controller' is granted Tasks read via seed.controller_role.sql (already
-- updated when the Module was renamed from 'Pending Actions').
-- System admins bypass module checks via User.IsSystemAdmin = 1.
--
-- Idempotent (MERGE). Safe to re-run. Owner gets the grant by re-running
-- intelligence/persistence/sql/seed.owner_role.sql after this lands.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py entities/role/sql/migrations/003_grant_tasks_module.sql

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @Now      DATETIME2(3) = SYSUTCDATETIME();
DECLARE @TasksId  BIGINT       = (SELECT [Id] FROM dbo.[Module] WHERE [Name] = N'Tasks');

IF @TasksId IS NULL
BEGIN
    RAISERROR('Tasks Module row missing — run entities/module/sql/seed.AllModules.sql first.', 16, 1);
    RETURN;
END;

-- ---------------------------------------------------------------------
-- Tenant Admin: full CRUD (matches the "unrestricted operator" pattern)
-- ---------------------------------------------------------------------
DECLARE @TenantAdminId BIGINT = (SELECT [Id] FROM dbo.[Role] WHERE [Name] = N'Tenant Admin');
IF @TenantAdminId IS NOT NULL
BEGIN
    MERGE dbo.[RoleModule] AS target
    USING (SELECT @TenantAdminId AS RoleId, @TasksId AS ModuleId) AS src
    ON target.[RoleId] = src.RoleId AND target.[ModuleId] = src.ModuleId
    WHEN MATCHED THEN
        UPDATE SET [CanCreate] = 1, [CanRead] = 1, [CanUpdate] = 1, [CanDelete] = 1,
                   [CanSubmit] = 1, [CanApprove] = 1, [CanComplete] = 1,
                   [ModifiedDatetime] = @Now
    WHEN NOT MATCHED THEN
        INSERT ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
                [CanCreate], [CanRead], [CanUpdate], [CanDelete],
                [CanSubmit], [CanApprove], [CanComplete])
        VALUES (@Now, @Now, src.RoleId, src.ModuleId,
                1, 1, 1, 1, 1, 1, 1);
    PRINT '  Tenant Admin: Tasks full CRUD merged';
END
ELSE
BEGIN
    PRINT '  Tenant Admin role missing — skipping.';
END

-- ---------------------------------------------------------------------
-- Read-only grants: Project Manager, Reviewer, AP Specialist, AR Specialist
-- ---------------------------------------------------------------------
DECLARE @ReadRoles TABLE ([Name] NVARCHAR(100));
INSERT INTO @ReadRoles ([Name]) VALUES
    (N'Project Manager'),
    (N'Reviewer'),
    (N'AP Specialist'),
    (N'AR Specialist');

MERGE dbo.[RoleModule] AS target
USING (
    SELECT r.[Id] AS RoleId, @TasksId AS ModuleId
    FROM dbo.[Role] r
    INNER JOIN @ReadRoles rr ON rr.[Name] = r.[Name]
) AS src
ON target.[RoleId] = src.RoleId AND target.[ModuleId] = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET [CanRead] = 1, [ModifiedDatetime] = @Now
WHEN NOT MATCHED THEN
    INSERT ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
            [CanCreate], [CanRead], [CanUpdate], [CanDelete],
            [CanSubmit], [CanApprove], [CanComplete])
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            0, 1, 0, 0, 0, 0, 0);

DECLARE @Granted INT = @@ROWCOUNT;
PRINT CONCAT('  Read-only roles (PM / Reviewer / AP / AR): ', @Granted, ' grants merged');

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Tasks Module grants seeded for human roles.';
PRINT '       Re-run seed.owner_role.sql to propagate to Owner.';
PRINT '────────────────────────────────────────────────────────────';
