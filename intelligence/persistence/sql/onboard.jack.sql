-- Onboard Jack VanOrman as an Intern.
--
-- Intern role already exists and is pre-configured. Idempotent.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/onboard.jack.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

DECLARE @Username NVARCHAR(100)   = 'vanormanjv';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$/ValsHh1DNjzqyi7wZgfmOhES819z2HKXeu0zg80fUN7Ha6ZHyGfG';
DECLARE @Firstname NVARCHAR(50)   = 'Jack';
DECLARE @Lastname  NVARCHAR(255)  = 'VanOrman';
DECLARE @Email     NVARCHAR(255)  = 'jack.vanorman@student.cpalions.org';

DECLARE @RogersCompanyId BIGINT = (SELECT Id FROM dbo.Company      WHERE Name = 'Rogers Build, Inc.');
DECLARE @RogersOrgId     BIGINT = (SELECT Id FROM dbo.Organization WHERE Name = 'Rogers Build, Inc.');
DECLARE @InternRoleId    BIGINT = (SELECT Id FROM dbo.Role         WHERE Name = 'Intern');

IF @RogersCompanyId IS NULL OR @RogersOrgId IS NULL OR @InternRoleId IS NULL
BEGIN
    RAISERROR('Required Company/Organization/Role rows missing.', 16, 1);
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

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @InternRoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
    VALUES (@Now, @Now, @UserId, @InternRoleId, @RogersCompanyId);
    PRINT '  user-role link created (Intern)';
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

DECLARE @Cutoff DATETIME2 = DATEADD(MONTH, -12, SYSUTCDATETIME());
;WITH ActiveProjectIds AS (
    SELECT DISTINCT ProjectId FROM dbo.BillLineItem          WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ExpenseLineItem       WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.Invoice               WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ContractLabor         WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ContractLaborLineItem WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.BillCreditLineItem    WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.TimeEntry             WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
)
INSERT INTO dbo.UserProject (CreatedDatetime, ModifiedDatetime, UserId, ProjectId)
SELECT @Now, @Now, @UserId, a.ProjectId
FROM ActiveProjectIds a
INNER JOIN dbo.Project p ON p.Id = a.ProjectId
WHERE NOT EXISTS (SELECT 1 FROM dbo.UserProject up WHERE up.UserId = @UserId AND up.ProjectId = a.ProjectId);

PRINT CONCAT('  user-project rows inserted: ', @@ROWCOUNT);
PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Jack onboarded.';
PRINT '────────────────────────────────────────────────────────────';
