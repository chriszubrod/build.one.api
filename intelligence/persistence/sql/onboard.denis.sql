-- Onboard Denis Izaguirre as an Intern.
--
-- Denis differs from the jack/westin templates: his dbo.[User] row already
-- exists (Id=51, created 2026-06-04 via the admin UI) but was left
-- half-provisioned — no Auth, UserCompany, UserRole, UserOrganization, or
-- UserProject rows, so login failed with "No company access" (2026-06-09
-- onboarding-lockout diagnosis, fix #8). Pin the existing row instead of
-- the Auth-join lookup so we don't create a duplicate User. Idempotent.
--
-- No Contact row is created — Denis's email is unknown; add it from the
-- User Profile page when known.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/onboard.denis.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

DECLARE @UserId BIGINT             = 51;
DECLARE @Username NVARCHAR(100)    = 'izaguirredi';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$X0tbZY20WMN0uZBM4e9ByeUmTXy1mfoNQm9uls3PoFPFtZjNO8zpe';

DECLARE @RogersCompanyId BIGINT = (SELECT Id FROM dbo.Company      WHERE Name = 'Rogers Build, Inc.');
DECLARE @RogersOrgId     BIGINT = (SELECT Id FROM dbo.Organization WHERE Name = 'Rogers Build, Inc.');
DECLARE @InternRoleId    BIGINT = (SELECT Id FROM dbo.Role         WHERE Name = 'Intern');

IF @RogersCompanyId IS NULL OR @RogersOrgId IS NULL OR @InternRoleId IS NULL
BEGIN
    RAISERROR('Required Company/Organization/Role rows missing.', 16, 1);
    RETURN;
END;

IF NOT EXISTS (
    SELECT 1 FROM dbo.[User]
    WHERE Id = @UserId AND Firstname = 'Denis' AND Lastname = 'Izaguirre'
)
BEGIN
    RAISERROR('User 51 is not Denis Izaguirre — aborting.', 16, 1);
    RETURN;
END;
PRINT CONCAT('  user verified (id=', @UserId, ')');

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
PRINT 'DONE — Denis onboarded.';
PRINT '────────────────────────────────────────────────────────────';
