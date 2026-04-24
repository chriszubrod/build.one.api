-- Provision the sub_cost_code_agent user, auth row, role, user-role link,
-- and per-module grants. Idempotent — safe to re-run; only inserts what's
-- missing.
--
-- BEFORE RUNNING:
--   Generate a bcrypt hash for the password you want this agent to use:
--     .venv/bin/python -c "import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass('pw: ').encode(), bcrypt.gensalt(12)).decode())"
--   Replace the placeholder string below with the result.
--   Then set on App Service Application Settings:
--     SUB_COST_CODE_AGENT_USERNAME=agent_sub_cost_code
--     SUB_COST_CODE_AGENT_PASSWORD=<the password you bcrypt'd>
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.sub_cost_code_agent.sql

DECLARE @Username NVARCHAR(100) = 'agent_sub_cost_code';
DECLARE @Firstname NVARCHAR(100) = 'Agent';
DECLARE @Lastname NVARCHAR(100) = 'SubCostCode';
DECLARE @RoleName NVARCHAR(100) = 'Sub Cost Code Specialist';

-- ┌─────────────────────────────────────────────────────────────────────┐
-- │ REPLACE THIS PLACEHOLDER WITH A REAL BCRYPT HASH BEFORE RUNNING.    │
-- └─────────────────────────────────────────────────────────────────────┘
DECLARE @PasswordHash NVARCHAR(255) = 'REPLACE_ME_WITH_BCRYPT_HASH';

DECLARE @Now DATETIME2 = SYSUTCDATETIME();

IF @PasswordHash = 'REPLACE_ME_WITH_BCRYPT_HASH'
BEGIN
    RAISERROR('Generate a bcrypt hash and replace the @PasswordHash placeholder before running this script.', 16, 1);
    RETURN;
END;

-- 1. User row -----------------------------------------------------------
DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, @Firstname, @Lastname);
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  user created: id=', @UserId);
END
ELSE
BEGIN
    PRINT CONCAT('  user exists: id=', @UserId);
END;

-- 2. Auth row -----------------------------------------------------------
DECLARE @AuthId BIGINT;
SELECT @AuthId = Id FROM dbo.Auth WHERE Username = @Username;

IF @AuthId IS NULL
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    SET @AuthId = SCOPE_IDENTITY();
    PRINT CONCAT('  auth created: id=', @AuthId);
END
ELSE
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Id = @AuthId;
    PRINT CONCAT('  auth exists: id=', @AuthId, ' (password updated)');
END;

-- 3. Role row -----------------------------------------------------------
DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;

IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  role created: id=', @RoleId);
END
ELSE
BEGIN
    PRINT CONCAT('  role exists: id=', @RoleId);
END;

-- 4. UserRole link ------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @UserId, @RoleId);
    PRINT '  user-role link created';
END
ELSE
BEGIN
    PRINT '  user-role link exists';
END;

-- 5. Module grants ------------------------------------------------------
-- Module 18 = Sub Cost Codes (full CRUD)
-- Module 21 = Cost Codes (read-only — needed for parent resolution)
DECLARE @SubCostCodeModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Sub Cost Codes');
DECLARE @CostCodeModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Cost Codes');

IF @SubCostCodeModuleId IS NULL OR @CostCodeModuleId IS NULL
BEGIN
    RAISERROR('Could not find Sub Cost Codes / Cost Codes module rows. Aborting grants.', 16, 1);
    RETURN;
END;

-- Sub Cost Codes — CRUD
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @SubCostCodeModuleId)
BEGIN
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @SubCostCodeModuleId;
    PRINT '  sub-cost-codes grant updated (CRUD)';
END
ELSE
BEGIN
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @SubCostCodeModuleId, 1, 1, 1, 1, 0, 0, 0);
    PRINT '  sub-cost-codes grant created (CRUD)';
END;

-- Cost Codes — read only
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @CostCodeModuleId)
BEGIN
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @CostCodeModuleId;
    PRINT '  cost-codes grant updated (read-only)';
END
ELSE
BEGIN
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @CostCodeModuleId, 0, 1, 0, 0, 0, 0, 0);
    PRINT '  cost-codes grant created (read-only)';
END;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set SUB_COST_CODE_AGENT_USERNAME / _PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
