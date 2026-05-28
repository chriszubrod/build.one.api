-- Onboard Randy Rogers as a Controller.
--
-- Prerequisite: seed.controller_role.sql must have been run (one-time).
-- Idempotent — safe to re-run.
--
-- Controllers are not Project-scoped via UserProject (no per-project
-- notification routing today). Skipping UserProject for Randy.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/onboard.randy.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

DECLARE @Username NVARCHAR(100)   = 'rogersrr';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$T2GVQANrEJNV5a1P2wEAsOhftZKP.5orDIt4wmDSzTHoiommTa82m';
DECLARE @Firstname NVARCHAR(50)   = 'Randy';
DECLARE @Lastname  NVARCHAR(255)  = 'Rogers';
DECLARE @Email     NVARCHAR(255)  = 'randy@rogersbuild.com';

DECLARE @RogersCompanyId BIGINT = (SELECT Id FROM dbo.Company      WHERE Name = 'Rogers Build, Inc.');
DECLARE @RogersOrgId     BIGINT = (SELECT Id FROM dbo.Organization WHERE Name = 'Rogers Build, Inc.');
DECLARE @ControllerRoleId BIGINT = (SELECT Id FROM dbo.Role        WHERE Name = 'Controller');

IF @RogersCompanyId IS NULL OR @RogersOrgId IS NULL OR @ControllerRoleId IS NULL
BEGIN
    RAISERROR('Required Company/Organization/Role rows missing. Run seed.controller_role.sql first.', 16, 1);
    RETURN;
END;

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname, IsSystemAdmin, IsAgent)
    VALUES (@Now, @Now, @Firstname, @Lastname, 0, 0);
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  user created (id=', @UserId, ')');
END
ELSE PRINT CONCAT('  user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth SET PasswordHash = @PasswordHash, ModifiedDatetime = @Now WHERE Username = @Username;
    PRINT '  auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  auth created';
END;

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @ControllerRoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
    VALUES (@Now, @Now, @UserId, @ControllerRoleId, @RogersCompanyId);
    PRINT '  user-role link created (Controller)';
END
ELSE PRINT '  user-role link exists';

IF NOT EXISTS (SELECT 1 FROM dbo.UserOrganization WHERE UserId = @UserId AND OrganizationId = @RogersOrgId)
BEGIN
    INSERT INTO dbo.UserOrganization (CreatedDatetime, ModifiedDatetime, UserId, OrganizationId)
    VALUES (@Now, @Now, @UserId, @RogersOrgId);
    PRINT '  user-organization link created';
END
ELSE PRINT '  user-organization link exists';

IF NOT EXISTS (SELECT 1 FROM dbo.UserCompany WHERE UserId = @UserId AND CompanyId = @RogersCompanyId)
BEGIN
    INSERT INTO dbo.UserCompany (CreatedDatetime, ModifiedDatetime, UserId, CompanyId)
    VALUES (@Now, @Now, @UserId, @RogersCompanyId);
    PRINT '  user-company link created';
END
ELSE PRINT '  user-company link exists';

IF NOT EXISTS (SELECT 1 FROM dbo.Contact WHERE UserId = @UserId AND Email = @Email)
BEGIN
    INSERT INTO dbo.Contact (CreatedDatetime, ModifiedDatetime, Email, UserId)
    VALUES (@Now, @Now, @Email, @UserId);
    PRINT '  contact created';
END
ELSE PRINT '  contact exists';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Randy onboarded.';
PRINT '────────────────────────────────────────────────────────────';
