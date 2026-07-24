-- Provisions the 'Employee Labor' Module row and read-only RoleModule grants
-- so the employee_labor / employee_labor_line_item API routers and the web
-- nav gate (Module Name 'Employee Labor') work for non-admin users in prod.
--
-- Scope assignment:
--   Controller       — CanRead = 1 ONLY; all other permission flags 0
--   Owner            — CanRead = 1 ONLY; all other permission flags 0
--   Tenant Admin     — CanRead = 1 ONLY; all other permission flags 0
--
-- Time Tracking Specialist is deliberately EXCLUDED — it holds no financial-
-- module reads and operates on TimeEntry upstream; separation-of-duties.
--
-- Owner is granted directly in this file (no seed.owner_role.sql re-run
-- needed; its MERGE has no not-matched-by-source clause).
-- System admins bypass module checks via User.IsSystemAdmin = 1.
--
-- The Module row is deliberately provisioned inline rather than as a separate
-- entities/module/sql/seed.EmployeeLaborModule.sql: row + grants + verification
-- land as ONE atomic, single-command unit, avoiding the multi-file ordering
-- hazard the Budgets rollout carried (005's OPS PREREQUISITES block).
--
-- Effect timing: direct SQL does not invalidate the in-proc RBAC cache —
-- non-admin access clears within the 5-minute cache TTL; nav appears on the
-- next /auth/me refresh.
--
-- Idempotent — safe to re-run.
-- Fails loudly (and the runner rolls back) if the module row or the 3 read grants do not verify after apply — a missing/renamed role can never end in a silent DONE.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py entities/role/sql/migrations/006_provision_employee_labor_module.sql

SET XACT_ABORT ON;
SET NOCOUNT ON;

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = N'Employee Labor')
BEGIN
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), N'Employee Labor', N'/employee-labor/list');
END

DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
DECLARE @ElId BIGINT = (SELECT [Id] FROM dbo.[Module] WHERE [Name] = N'Employee Labor');

IF @ElId IS NULL
BEGIN
    RAISERROR('Employee Labor Module row missing — INSERT above should have created it.', 16, 1);
    RETURN;
END;

DECLARE @GrantRoles TABLE ([Name] NVARCHAR(100));
INSERT INTO @GrantRoles ([Name]) VALUES
    (N'Controller'),
    (N'Owner'),
    (N'Tenant Admin');

INSERT INTO dbo.[RoleModule] ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId], [CanCreate], [CanRead], [CanUpdate], [CanDelete], [CanSubmit], [CanApprove], [CanComplete], [CanViewTeam])
SELECT @Now, @Now, r.[Id], @ElId, 0, 1, 0, 0, 0, 0, 0, 0
FROM dbo.[Role] r
INNER JOIN @GrantRoles gr ON gr.[Name] = r.[Name]
WHERE NOT EXISTS (SELECT 1 FROM dbo.[RoleModule] rm WHERE rm.[RoleId] = r.[Id] AND rm.[ModuleId] = @ElId);

DECLARE @Granted INT = @@ROWCOUNT;
PRINT CONCAT('  Read-only roles (Controller / Owner / Tenant Admin): ', @Granted, ' grants inserted');

DECLARE @MissingRoles NVARCHAR(MAX) = (
    SELECT STRING_AGG(gr.[Name], N', ')
    FROM @GrantRoles gr
    LEFT JOIN dbo.[Role] r ON r.[Name] = gr.[Name]
    WHERE r.[Id] IS NULL
);
IF @MissingRoles IS NOT NULL
    PRINT CONCAT('  Roles missing (verification below will fail the run): ', @MissingRoles);

-- Post-apply verification — fail loudly (severity 16 => run_sql.py surfaces the error and the connection ROLLS BACK the whole run) rather than print DONE on a partial result.
IF (SELECT COUNT(*) FROM dbo.[Module] WHERE [Name] = N'Employee Labor') <> 1
BEGIN
    RAISERROR('Verification failed: expected exactly 1 ''Employee Labor'' Module row.', 16, 1);
    RETURN;
END;

DECLARE @VerifiedGrants INT, @VerifiedRoleNames INT;
SELECT @VerifiedGrants = COUNT(*), @VerifiedRoleNames = COUNT(DISTINCT r.[Name])
FROM dbo.[RoleModule] rm
INNER JOIN dbo.[Role] r ON r.[Id] = rm.[RoleId]
INNER JOIN @GrantRoles gr ON gr.[Name] = r.[Name]
WHERE rm.[ModuleId] = @ElId
  AND rm.[CanRead] = 1;
IF @VerifiedGrants <> 3 OR @VerifiedRoleNames <> 3
BEGIN
    RAISERROR('Verification failed: expected exactly 1 CanRead grant per role (Controller / Owner / Tenant Admin) — found %d grant rows across %d distinct roles.', 16, 1, @VerifiedGrants, @VerifiedRoleNames);
    RETURN;
END;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Employee Labor Module row + read-only grants provisioned.';
PRINT '       Non-admin access effective within RBAC cache TTL (~5 min).';
PRINT '────────────────────────────────────────────────────────────';
GO
