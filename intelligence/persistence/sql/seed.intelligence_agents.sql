-- Provision the agent fleet's User + Auth + Role + grants. Idempotent.
--
-- Three agents:
--   scout_agent          — orchestrator. Renamed from legacy "agent_one".
--                          Keeps Admin role for now (still has direct
--                          tools alongside delegation). Phase 1B will
--                          narrow it once pure-delegation lands.
--   sub_cost_code_agent  — specialist. SubCostCode CRUD + CostCode read.
--   cost_code_agent      — specialist (credentials only — no agent code
--                          yet; ready for the future CostCodeSpecialist).
--                          CostCode CRUD + SubCostCode read.
--
-- The bcrypt hashes below were generated outside this file. Cleartext
-- passwords are stored in .env / Azure App Service Application Settings.
-- Regenerating: see the one-liner in scripts/run_sql.py's docstring or
-- ask Claude (one-time generation, not committed).
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.intelligence_agents.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

-- ─── Helper variables ────────────────────────────────────────────────────
DECLARE @SubCostCodeModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Sub Cost Codes');
DECLARE @CostCodeModuleId    INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Cost Codes');
DECLARE @AdminRoleId         BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Admin');

IF @SubCostCodeModuleId IS NULL OR @CostCodeModuleId IS NULL OR @AdminRoleId IS NULL
BEGIN
    RAISERROR('Required module/role rows missing. Aborting.', 16, 1);
    RETURN;
END;


-- ════════════════════════════════════════════════════════════════════════
-- 1. scout_agent — rename from agent_one + new password
-- ════════════════════════════════════════════════════════════════════════

DECLARE @ScoutPasswordHash NVARCHAR(255) =
    '$2b$12$cpEJ6KUuoIGw6Z5vJ1Lhlu9W88AIYR8mSsfXygoiYeph1fZFA2tfa';

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = 'agent_one')
BEGIN
    UPDATE dbo.Auth
       SET Username = 'scout_agent',
           PasswordHash = @ScoutPasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = 'agent_one';
    PRINT '  scout: renamed agent_one -> scout_agent (password rotated)';
END
ELSE IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = 'scout_agent')
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @ScoutPasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = 'scout_agent';
    PRINT '  scout: scout_agent exists (password rotated)';
END
ELSE
BEGIN
    PRINT '  scout: NEITHER agent_one NOR scout_agent found — skipped (no User row to rename)';
END;


-- ════════════════════════════════════════════════════════════════════════
-- 2. sub_cost_code_agent — full CRUD on SubCostCode, read on CostCode
-- ════════════════════════════════════════════════════════════════════════

DECLARE @SubAgentUsername NVARCHAR(100)   = 'sub_cost_code_agent';
DECLARE @SubAgentRoleName NVARCHAR(100)   = 'Sub Cost Code Specialist';
DECLARE @SubAgentPasswordHash NVARCHAR(255) =
    '$2b$12$WgQTnBb0XMvY6Xkiq68QbeBuoELkKh/vZVIPLGK41anWiUjLPwEQi';

DECLARE @SubUserId BIGINT;
SELECT @SubUserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @SubAgentUsername;

IF @SubUserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'SubCostCode');
    SET @SubUserId = SCOPE_IDENTITY();
    PRINT CONCAT('  sub_cost_code_agent: user created (id=', @SubUserId, ')');
END
ELSE
BEGIN
    PRINT CONCAT('  sub_cost_code_agent: user exists (id=', @SubUserId, ')');
END;

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @SubAgentUsername)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @SubAgentPasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @SubAgentUsername;
    PRINT '  sub_cost_code_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @SubAgentUsername, @SubAgentPasswordHash, @SubUserId);
    PRINT '  sub_cost_code_agent: auth created';
END;

DECLARE @SubRoleId BIGINT;
SELECT @SubRoleId = Id FROM dbo.Role WHERE Name = @SubAgentRoleName;
IF @SubRoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @SubAgentRoleName);
    SET @SubRoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  sub_cost_code_agent: role created (id=', @SubRoleId, ')');
END
ELSE
BEGIN
    PRINT CONCAT('  sub_cost_code_agent: role exists (id=', @SubRoleId, ')');
END;

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @SubUserId AND RoleId = @SubRoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @SubUserId, @SubRoleId);
    PRINT '  sub_cost_code_agent: user-role link created';
END;

IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @SubRoleId AND ModuleId = @SubCostCodeModuleId)
BEGIN
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @SubRoleId AND ModuleId = @SubCostCodeModuleId;
END
ELSE
BEGIN
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @SubRoleId, @SubCostCodeModuleId, 1, 1, 1, 1, 0, 0, 0);
END;

IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @SubRoleId AND ModuleId = @CostCodeModuleId)
BEGIN
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @SubRoleId AND ModuleId = @CostCodeModuleId;
END
ELSE
BEGIN
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @SubRoleId, @CostCodeModuleId, 0, 1, 0, 0, 0, 0, 0);
END;
PRINT '  sub_cost_code_agent: module grants set (sub_cost_code CRUD, cost_code read)';


-- ════════════════════════════════════════════════════════════════════════
-- 3. cost_code_agent — full CRUD on CostCode, read on SubCostCode
--    No agent code uses this yet; credentials staged for future use.
-- ════════════════════════════════════════════════════════════════════════

DECLARE @CostAgentUsername NVARCHAR(100)   = 'cost_code_agent';
DECLARE @CostAgentRoleName NVARCHAR(100)   = 'Cost Code Specialist';
DECLARE @CostAgentPasswordHash NVARCHAR(255) =
    '$2b$12$OkbuZuG2jHbmxVQbkQIOPO1lw11L0F20iSYDLjMPTCdehUMoNzKKy';

DECLARE @CostUserId BIGINT;
SELECT @CostUserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @CostAgentUsername;

IF @CostUserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'CostCode');
    SET @CostUserId = SCOPE_IDENTITY();
    PRINT CONCAT('  cost_code_agent: user created (id=', @CostUserId, ')');
END
ELSE
BEGIN
    PRINT CONCAT('  cost_code_agent: user exists (id=', @CostUserId, ')');
END;

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @CostAgentUsername)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @CostAgentPasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @CostAgentUsername;
    PRINT '  cost_code_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @CostAgentUsername, @CostAgentPasswordHash, @CostUserId);
    PRINT '  cost_code_agent: auth created';
END;

DECLARE @CostRoleId BIGINT;
SELECT @CostRoleId = Id FROM dbo.Role WHERE Name = @CostAgentRoleName;
IF @CostRoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @CostAgentRoleName);
    SET @CostRoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  cost_code_agent: role created (id=', @CostRoleId, ')');
END
ELSE
BEGIN
    PRINT CONCAT('  cost_code_agent: role exists (id=', @CostRoleId, ')');
END;

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @CostUserId AND RoleId = @CostRoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @CostUserId, @CostRoleId);
    PRINT '  cost_code_agent: user-role link created';
END;

IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @CostRoleId AND ModuleId = @CostCodeModuleId)
BEGIN
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @CostRoleId AND ModuleId = @CostCodeModuleId;
END
ELSE
BEGIN
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @CostRoleId, @CostCodeModuleId, 1, 1, 1, 1, 0, 0, 0);
END;

IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @CostRoleId AND ModuleId = @SubCostCodeModuleId)
BEGIN
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @CostRoleId AND ModuleId = @SubCostCodeModuleId;
END
ELSE
BEGIN
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @CostRoleId, @SubCostCodeModuleId, 0, 1, 0, 0, 0, 0, 0);
END;
PRINT '  cost_code_agent: module grants set (cost_code CRUD, sub_cost_code read)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set the matching env vars on App Service Application Settings.';
PRINT '────────────────────────────────────────────────────────────';
