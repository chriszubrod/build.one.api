-- Provision the "claude_agent" User + Auth + Role link + Org/Company links.
-- Mirrors buildone_agent's shape: Agent Orchestrator role (zero module grants),
-- Rogers Build, Inc. company, no Contact row, no UserModule rows.
-- One additive change vs buildone: a UserOrganization row to Rogers Build, Inc.
--
-- Idempotent — safe to re-run.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.claude_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

DECLARE @Username NVARCHAR(100) = 'claude_agent';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$j7IBS9wKyKK8fwtm0sRSDeVeznfP.HGCavCW1R36U/scktLDwl.Tu';

DECLARE @RogersCompanyId BIGINT      = (SELECT Id FROM dbo.Company      WHERE Name = 'Rogers Build, Inc.');
DECLARE @RogersOrgId     BIGINT      = (SELECT Id FROM dbo.Organization WHERE Name = 'Rogers Build, Inc.');
DECLARE @OrchestratorRoleId BIGINT   = (SELECT Id FROM dbo.Role         WHERE Name = 'Agent Orchestrator');

IF @RogersCompanyId IS NULL OR @RogersOrgId IS NULL OR @OrchestratorRoleId IS NULL
BEGIN
    RAISERROR('Required Company/Organization/Role rows missing. Aborting.', 16, 1);
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
    VALUES (@Now, @Now, 'Claude', 'Agent', 0, 1);
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

-- ─── 3. UserRole link (Agent Orchestrator) ──────────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM dbo.UserRole
     WHERE UserId = @UserId AND RoleId = @OrchestratorRoleId
)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
    VALUES (@Now, @Now, @UserId, @OrchestratorRoleId, @RogersCompanyId);
    PRINT '  user-role link created (Agent Orchestrator)';
END
ELSE
BEGIN
    PRINT '  user-role link exists (Agent Orchestrator)';
END;

-- ─── 4. UserCompany link (Rogers Build, Inc.) ───────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM dbo.UserCompany
     WHERE UserId = @UserId AND CompanyId = @RogersCompanyId
)
BEGIN
    INSERT INTO dbo.UserCompany (CreatedDatetime, ModifiedDatetime, UserId, CompanyId)
    VALUES (@Now, @Now, @UserId, @RogersCompanyId);
    PRINT '  user-company link created (Rogers Build, Inc.)';
END
ELSE
BEGIN
    PRINT '  user-company link exists (Rogers Build, Inc.)';
END;

-- ─── 5. UserOrganization link (Rogers Build, Inc.) ──────────────────────
IF NOT EXISTS (
    SELECT 1 FROM dbo.UserOrganization
     WHERE UserId = @UserId AND OrganizationId = @RogersOrgId
)
BEGIN
    INSERT INTO dbo.UserOrganization (CreatedDatetime, ModifiedDatetime, UserId, OrganizationId)
    VALUES (@Now, @Now, @UserId, @RogersOrgId);
    PRINT '  user-organization link created (Rogers Build, Inc.)';
END
ELSE
BEGIN
    PRINT '  user-organization link exists (Rogers Build, Inc.)';
END;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — claude_agent provisioned.';
PRINT '────────────────────────────────────────────────────────────';
