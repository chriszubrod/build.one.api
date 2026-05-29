-- Onboard Brayan Rafael Marcia Salina as a Field Crew user.
--
-- Prerequisite: seed.field_crew_role.sql must have been run.
-- Idempotent — safe to re-run.
--
-- Sets up:
--   User + Auth + UserRole(Field Crew) + UserOrganization(Rogers) +
--   UserCompany(Rogers) + Contact(email) + UserProject (one row per
--   active project: any Project with line-item / time-entry activity
--   in the last 12 months).
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/onboard.brayan.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

DECLARE @Username NVARCHAR(100)   = 'marciabm';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$U40WZNg9/pW4aoP6wDJpHu7701MXxB532oAQXrnDsbfWmAmuT0qeK';
DECLARE @Firstname NVARCHAR(50)   = 'Brayan';
DECLARE @Lastname  NVARCHAR(255)  = 'Marcia Salina';
DECLARE @Email     NVARCHAR(255)  = 'mbrayan1019@icloud.com';

DECLARE @RogersCompanyId BIGINT = (SELECT Id FROM dbo.Company      WHERE Name = 'Rogers Build, Inc.');
DECLARE @RogersOrgId     BIGINT = (SELECT Id FROM dbo.Organization WHERE Name = 'Rogers Build, Inc.');
DECLARE @FieldCrewRoleId BIGINT = (SELECT Id FROM dbo.Role         WHERE Name = 'Field Crew');

IF @RogersCompanyId IS NULL OR @RogersOrgId IS NULL OR @FieldCrewRoleId IS NULL
BEGIN
    RAISERROR('Required Company/Organization/Role rows missing. Run seed.field_crew_role.sql first.', 16, 1);
    RETURN;
END;

-- ─── 1. User row ─────────────────────────────────────────────────────────
DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname, IsSystemAdmin, IsAgent)
    VALUES (@Now, @Now, @Firstname, @Lastname, 0, 0);
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  user created (id=', @UserId, ')');
END
ELSE
BEGIN
    PRINT CONCAT('  user exists (id=', @UserId, ')');
END;

-- ─── 2. Auth row ─────────────────────────────────────────────────────────
IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  auth created';
END;

-- ─── 3. UserRole link (Field Crew, Rogers Company) ──────────────────────
IF NOT EXISTS (
    SELECT 1 FROM dbo.UserRole
     WHERE UserId = @UserId AND RoleId = @FieldCrewRoleId
)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
    VALUES (@Now, @Now, @UserId, @FieldCrewRoleId, @RogersCompanyId);
    PRINT '  user-role link created (Field Crew)';
END
ELSE
BEGIN
    PRINT '  user-role link exists (Field Crew)';
END;

-- ─── 4. UserOrganization link ───────────────────────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM dbo.UserOrganization
     WHERE UserId = @UserId AND OrganizationId = @RogersOrgId
)
BEGIN
    INSERT INTO dbo.UserOrganization (CreatedDatetime, ModifiedDatetime, UserId, OrganizationId)
    VALUES (@Now, @Now, @UserId, @RogersOrgId);
    PRINT '  user-organization link created';
END
ELSE
BEGIN
    PRINT '  user-organization link exists';
END;

-- ─── 5. UserCompany link ────────────────────────────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM dbo.UserCompany
     WHERE UserId = @UserId AND CompanyId = @RogersCompanyId
)
BEGIN
    INSERT INTO dbo.UserCompany (CreatedDatetime, ModifiedDatetime, UserId, CompanyId)
    VALUES (@Now, @Now, @UserId, @RogersCompanyId);
    PRINT '  user-company link created';
END
ELSE
BEGIN
    PRINT '  user-company link exists';
END;

-- ─── 6. Contact row (email) ─────────────────────────────────────────────
IF NOT EXISTS (SELECT 1 FROM dbo.Contact WHERE UserId = @UserId AND Email = @Email)
BEGIN
    INSERT INTO dbo.Contact (CreatedDatetime, ModifiedDatetime, Email, UserId)
    VALUES (@Now, @Now, @Email, @UserId);
    PRINT '  contact created';
END
ELSE
BEGIN
    PRINT '  contact exists';
END;

-- ─── 7. UserProject bulk (active projects only) ─────────────────────────
-- "Active" = any line item / time-entry / invoice / contract-labor row
-- created against the project in the last 12 months. Idempotent: skips
-- projects already linked for this user.
DECLARE @Cutoff DATETIME2 = DATEADD(MONTH, -12, SYSUTCDATETIME());

;WITH ActiveProjectIds AS (
    SELECT DISTINCT ProjectId FROM dbo.BillLineItem        WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ExpenseLineItem     WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.Invoice             WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ContractLabor       WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ContractLaborLineItem WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.BillCreditLineItem  WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.TimeEntry           WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
)
INSERT INTO dbo.UserProject (CreatedDatetime, ModifiedDatetime, UserId, ProjectId)
SELECT @Now, @Now, @UserId, a.ProjectId
FROM ActiveProjectIds a
INNER JOIN dbo.Project p ON p.Id = a.ProjectId
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.UserProject up
     WHERE up.UserId = @UserId AND up.ProjectId = a.ProjectId
);

DECLARE @InsertedProjects INT = @@ROWCOUNT;
PRINT CONCAT('  user-project rows inserted: ', @InsertedProjects);

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Brayan onboarded.';
PRINT '────────────────────────────────────────────────────────────';
