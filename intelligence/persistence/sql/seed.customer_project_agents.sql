-- Provision customer_agent + project_agent users, auth rows, narrow roles,
-- and per-module grants. Idempotent — safe to re-run.
--
-- Mirrors the sub_cost_code_agent / cost_code_agent provisioning pattern.
-- Companion file: seed.intelligence_agents.sql.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.customer_project_agents.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

-- ─── Module + role lookups ───────────────────────────────────────────────
DECLARE @CustomerModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Customers');
DECLARE @ProjectModuleId  INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Projects');

IF @CustomerModuleId IS NULL OR @ProjectModuleId IS NULL
BEGIN
    RAISERROR('Required Module rows (Customers / Projects) missing. Aborting.', 16, 1);
    RETURN;
END;


-- ════════════════════════════════════════════════════════════════════════
-- 1. customer_agent — Customers CRUD + Projects read
-- ════════════════════════════════════════════════════════════════════════

DECLARE @CustomerAgentUsername NVARCHAR(100) = 'customer_agent';
DECLARE @CustomerAgentRoleName NVARCHAR(100) = 'Customer Specialist';
DECLARE @CustomerAgentPasswordHash NVARCHAR(255) =
    '$2b$12$QycYJWPKzAgGwVEvUQYa2.8PnlNTkGFLSwE6kAw/XPrnkYT7xVLJK';

DECLARE @CustomerUserId BIGINT;
SELECT @CustomerUserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @CustomerAgentUsername;

IF @CustomerUserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'Customer');
    SET @CustomerUserId = SCOPE_IDENTITY();
    PRINT CONCAT('  customer_agent: user created (id=', @CustomerUserId, ')');
END
ELSE
    PRINT CONCAT('  customer_agent: user exists (id=', @CustomerUserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @CustomerAgentUsername)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @CustomerAgentPasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @CustomerAgentUsername;
    PRINT '  customer_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @CustomerAgentUsername, @CustomerAgentPasswordHash, @CustomerUserId);
    PRINT '  customer_agent: auth created';
END;

DECLARE @CustomerRoleId BIGINT;
SELECT @CustomerRoleId = Id FROM dbo.Role WHERE Name = @CustomerAgentRoleName;
IF @CustomerRoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @CustomerAgentRoleName);
    SET @CustomerRoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  customer_agent: role created (id=', @CustomerRoleId, ')');
END
ELSE
    PRINT CONCAT('  customer_agent: role exists (id=', @CustomerRoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @CustomerUserId AND RoleId = @CustomerRoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @CustomerUserId, @CustomerRoleId);
    PRINT '  customer_agent: user-role link created';
END;

-- Customers — full CRUD
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @CustomerRoleId AND ModuleId = @CustomerModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @CustomerRoleId AND ModuleId = @CustomerModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @CustomerRoleId, @CustomerModuleId, 1, 1, 1, 1, 0, 0, 0);

-- Projects — read only
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @CustomerRoleId AND ModuleId = @ProjectModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @CustomerRoleId AND ModuleId = @ProjectModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @CustomerRoleId, @ProjectModuleId, 0, 1, 0, 0, 0, 0, 0);

PRINT '  customer_agent: module grants set (customers CRUD, projects read)';


-- ════════════════════════════════════════════════════════════════════════
-- 2. project_agent — Projects CRUD + Customers read
-- ════════════════════════════════════════════════════════════════════════

DECLARE @ProjectAgentUsername NVARCHAR(100) = 'project_agent';
DECLARE @ProjectAgentRoleName NVARCHAR(100) = 'Project Specialist';
DECLARE @ProjectAgentPasswordHash NVARCHAR(255) =
    '$2b$12$xHQfyuLEU3wNx3rt2selH.UUIokFHrdgKqprHKuRi4./iMJ2XgSBy';

DECLARE @ProjectUserId BIGINT;
SELECT @ProjectUserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @ProjectAgentUsername;

IF @ProjectUserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'Project');
    SET @ProjectUserId = SCOPE_IDENTITY();
    PRINT CONCAT('  project_agent: user created (id=', @ProjectUserId, ')');
END
ELSE
    PRINT CONCAT('  project_agent: user exists (id=', @ProjectUserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @ProjectAgentUsername)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @ProjectAgentPasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @ProjectAgentUsername;
    PRINT '  project_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @ProjectAgentUsername, @ProjectAgentPasswordHash, @ProjectUserId);
    PRINT '  project_agent: auth created';
END;

DECLARE @ProjectRoleId BIGINT;
SELECT @ProjectRoleId = Id FROM dbo.Role WHERE Name = @ProjectAgentRoleName;
IF @ProjectRoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @ProjectAgentRoleName);
    SET @ProjectRoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  project_agent: role created (id=', @ProjectRoleId, ')');
END
ELSE
    PRINT CONCAT('  project_agent: role exists (id=', @ProjectRoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @ProjectUserId AND RoleId = @ProjectRoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @ProjectUserId, @ProjectRoleId);
    PRINT '  project_agent: user-role link created';
END;

-- Projects — full CRUD
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @ProjectRoleId AND ModuleId = @ProjectModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @ProjectRoleId AND ModuleId = @ProjectModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @ProjectRoleId, @ProjectModuleId, 1, 1, 1, 1, 0, 0, 0);

-- Customers — read only
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @ProjectRoleId AND ModuleId = @CustomerModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @ProjectRoleId AND ModuleId = @CustomerModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @ProjectRoleId, @CustomerModuleId, 0, 1, 0, 0, 0, 0, 0);

PRINT '  project_agent: module grants set (projects CRUD, customers read)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set CUSTOMER_AGENT_USERNAME/PASSWORD and PROJECT_AGENT_USERNAME/PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
