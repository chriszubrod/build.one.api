-- Seed 7 persona test accounts covering every real role we have, plus an
-- IsSystemAdmin-bypass persona. Idempotent — safe to re-run.
--
--   persona_owner            — Owner role
--   persona_tenant_admin     — Tenant Admin role
--   persona_controller       — Controller role
--   persona_project_manager  — Project Manager role
--   persona_field_crew       — Field Crew role
--   persona_intern           — Intern role
--   persona_sysadmin         — IsSystemAdmin=true (no role assigned)
--
-- Each is linked to Rogers Build, Inc. (Organization + Company) with all 129
-- active projects in UserProject. RoleId is set per-row for personas where it
-- matters (Owner / Project Manager — drives bill-submit notification routing).
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.personas.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @Cutoff DATETIME2 = DATEADD(MONTH, -12, SYSUTCDATETIME());

DECLARE @RogersCompanyId BIGINT = (SELECT Id FROM dbo.Company      WHERE Name = 'Rogers Build, Inc.');
DECLARE @RogersOrgId     BIGINT = (SELECT Id FROM dbo.Organization WHERE Name = 'Rogers Build, Inc.');

IF @RogersCompanyId IS NULL OR @RogersOrgId IS NULL
BEGIN
    RAISERROR('Rogers Build company/organization rows missing.', 16, 1);
    RETURN;
END;

-- Persona definitions table
DECLARE @Personas TABLE (
    Username NVARCHAR(100),
    Firstname NVARCHAR(50),
    Lastname NVARCHAR(255),
    Email NVARCHAR(255),
    PasswordHash NVARCHAR(255),
    RoleName NVARCHAR(100) NULL,
    IsSystemAdmin BIT,
    StampRoleOnUserProject BIT
);

INSERT INTO @Personas VALUES
    ('persona_owner',           'Persona', 'Owner',           'persona.owner@buildone.test',
     '$2b$12$ZWT4cuWLPkZPFKBYJeYY/.woBwHmiuegHMsdTyY85IYFbhOZQMmPa', 'Owner',           0, 1),
    ('persona_tenant_admin',    'Persona', 'Tenant Admin',    'persona.tenant.admin@buildone.test',
     '$2b$12$bUKSgQWx2VXjIiDeMYLx/eweluihaiqvfg8FGr8Qn1M9VEZ5uKycq', 'Tenant Admin',    0, 0),
    ('persona_controller',      'Persona', 'Controller',      'persona.controller@buildone.test',
     '$2b$12$huPIzqxP1CqVBZvHGGth.eMIrbngPp4ToqsN9phVEL06nVyA34rny', 'Controller',      0, 0),
    ('persona_project_manager', 'Persona', 'Project Manager', 'persona.project.manager@buildone.test',
     '$2b$12$vFpx0/x/4RsJJ2pddZp70erLmGjIrjF9ip4qMY0Tj2xE9LriB.XDa', 'Project Manager', 0, 1),
    ('persona_field_crew',      'Persona', 'Field Crew',      'persona.field.crew@buildone.test',
     '$2b$12$Afy22FFbZwMPoL2ySvKkBe8PyJWwbSgeM.IlF6VUW9FE8QCqzUhty', 'Field Crew',      0, 0),
    ('persona_intern',          'Persona', 'Intern',          'persona.intern@buildone.test',
     '$2b$12$qdub9FWkE.HF1F/fBGfRsuyHU1hvGU/DZa/MMI8XOLgfnmulaF4nW', 'Intern',          0, 0),
    ('persona_sysadmin',        'Persona', 'Sysadmin',        'persona.sysadmin@buildone.test',
     '$2b$12$F1O2pe9XePx/0eTjsF9jyOyUQpbf/MnTu3Hx.5b3WhUy2DiRP9cjC', NULL,              1, 0);

-- Resolve active project Ids once
DECLARE @ActiveProjects TABLE (ProjectId BIGINT PRIMARY KEY);
INSERT INTO @ActiveProjects
SELECT DISTINCT ProjectId FROM (
    SELECT ProjectId FROM dbo.BillLineItem          WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT ProjectId FROM dbo.ExpenseLineItem       WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT ProjectId FROM dbo.Invoice               WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT ProjectId FROM dbo.ContractLabor         WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT ProjectId FROM dbo.ContractLaborLineItem WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT ProjectId FROM dbo.BillCreditLineItem    WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT ProjectId FROM dbo.TimeLog               WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
) src;

-- Loop over personas
DECLARE @Username NVARCHAR(100), @Firstname NVARCHAR(50), @Lastname NVARCHAR(255),
        @Email NVARCHAR(255), @PasswordHash NVARCHAR(255), @RoleName NVARCHAR(100),
        @IsSystemAdmin BIT, @StampRoleOnUP BIT;

DECLARE persona_cur CURSOR LOCAL FAST_FORWARD FOR
    SELECT Username, Firstname, Lastname, Email, PasswordHash, RoleName, IsSystemAdmin, StampRoleOnUserProject
    FROM @Personas;

OPEN persona_cur;
FETCH NEXT FROM persona_cur INTO @Username, @Firstname, @Lastname, @Email, @PasswordHash, @RoleName, @IsSystemAdmin, @StampRoleOnUP;

DECLARE @UserId BIGINT, @RoleId BIGINT, @RoleIdForUP BIGINT;

WHILE @@FETCH_STATUS = 0
BEGIN
    -- T-SQL hoists DECLARE inside loops to batch scope, so variables persist
    -- across iterations. Reset them explicitly before each persona.
    SET @UserId      = NULL;
    SET @RoleId      = NULL;
    SET @RoleIdForUP = NULL;

    -- Resolve role id (NULL for sysadmin)
    SET @RoleId = CASE WHEN @RoleName IS NULL THEN NULL
                       ELSE (SELECT Id FROM dbo.Role WHERE Name = @RoleName) END;

    IF @RoleName IS NOT NULL AND @RoleId IS NULL
    BEGIN
        PRINT CONCAT('  WARN: role "', @RoleName, '" not found — skipping ', @Username);
        FETCH NEXT FROM persona_cur INTO @Username, @Firstname, @Lastname, @Email, @PasswordHash, @RoleName, @IsSystemAdmin, @StampRoleOnUP;
        CONTINUE;
    END;

    -- User row
    SELECT @UserId = u.Id FROM dbo.[User] u
    INNER JOIN dbo.Auth a ON a.UserId = u.Id WHERE a.Username = @Username;

    IF @UserId IS NULL
    BEGIN
        INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname, IsSystemAdmin, IsAgent)
        VALUES (@Now, @Now, @Firstname, @Lastname, @IsSystemAdmin, 0);
        SET @UserId = SCOPE_IDENTITY();
        PRINT CONCAT('  ', @Username, ': user created (id=', @UserId, ')');
    END
    ELSE
    BEGIN
        UPDATE dbo.[User] SET IsSystemAdmin = @IsSystemAdmin, ModifiedDatetime = @Now WHERE Id = @UserId;
        PRINT CONCAT('  ', @Username, ': user exists (id=', @UserId, ', IsSystemAdmin synced)');
    END;

    -- Auth
    IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
        UPDATE dbo.Auth SET PasswordHash = @PasswordHash, ModifiedDatetime = @Now WHERE Username = @Username;
    ELSE
        INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
        VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);

    -- UserRole (skip if sysadmin/no role)
    IF @RoleId IS NOT NULL AND NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId)
        INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
        VALUES (@Now, @Now, @UserId, @RoleId, @RogersCompanyId);

    -- UserOrganization
    IF NOT EXISTS (SELECT 1 FROM dbo.UserOrganization WHERE UserId = @UserId AND OrganizationId = @RogersOrgId)
        INSERT INTO dbo.UserOrganization (CreatedDatetime, ModifiedDatetime, UserId, OrganizationId)
        VALUES (@Now, @Now, @UserId, @RogersOrgId);

    -- UserCompany
    IF NOT EXISTS (SELECT 1 FROM dbo.UserCompany WHERE UserId = @UserId AND CompanyId = @RogersCompanyId)
        INSERT INTO dbo.UserCompany (CreatedDatetime, ModifiedDatetime, UserId, CompanyId)
        VALUES (@Now, @Now, @UserId, @RogersCompanyId);

    -- Contact
    IF NOT EXISTS (SELECT 1 FROM dbo.Contact WHERE UserId = @UserId AND Email = @Email)
        INSERT INTO dbo.Contact (CreatedDatetime, ModifiedDatetime, Email, UserId)
        VALUES (@Now, @Now, @Email, @UserId);

    -- UserProject for all active projects (RoleId stamped per persona policy)
    SET @RoleIdForUP = CASE WHEN @StampRoleOnUP = 1 THEN @RoleId ELSE NULL END;
    INSERT INTO dbo.UserProject (CreatedDatetime, ModifiedDatetime, UserId, ProjectId, RoleId)
    SELECT @Now, @Now, @UserId, ap.ProjectId, @RoleIdForUP
    FROM @ActiveProjects ap
    INNER JOIN dbo.Project p ON p.Id = ap.ProjectId
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.UserProject up
         WHERE up.UserId = @UserId AND up.ProjectId = ap.ProjectId
    );

    PRINT CONCAT('  ', @Username, ': onboarding complete');

    FETCH NEXT FROM persona_cur INTO @Username, @Firstname, @Lastname, @Email, @PasswordHash, @RoleName, @IsSystemAdmin, @StampRoleOnUP;
END;

CLOSE persona_cur;
DEALLOCATE persona_cur;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — 7 persona accounts seeded.';
PRINT '────────────────────────────────────────────────────────────';
