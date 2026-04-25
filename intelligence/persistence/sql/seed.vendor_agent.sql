-- Provision vendor_agent user, auth, role, and grants. Idempotent.
--
-- Mirrors customer_agent / project_agent / cost_code_agent pattern.
-- Companion: seed.intelligence_agents.sql, seed.customer_project_agents.sql.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.vendor_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @VendorModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Vendors');

IF @VendorModuleId IS NULL
BEGIN
    RAISERROR('Required Module row (Vendors) missing. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @Username NVARCHAR(100) = 'vendor_agent';
DECLARE @RoleName NVARCHAR(100) = 'Vendor Specialist';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$RG6WpPkydzEASa/LI/ZpAOBGe80CAGch1Hi2rDaVUICRD2u7OvY1W';

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'Vendor');
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  vendor_agent: user created (id=', @UserId, ')');
END
ELSE
    PRINT CONCAT('  vendor_agent: user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  vendor_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  vendor_agent: auth created';
END;

DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  vendor_agent: role created (id=', @RoleId, ')');
END
ELSE
    PRINT CONCAT('  vendor_agent: role exists (id=', @RoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @UserId, @RoleId);
    PRINT '  vendor_agent: user-role link created';
END;

-- Vendors — full CRUD (the Vendors module gates create/update/delete;
-- soft-delete on the server side requires CanDelete grant).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @VendorModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @VendorModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @VendorModuleId, 1, 1, 1, 1, 0, 0, 0);

PRINT '  vendor_agent: module grants set (Vendors CRUD)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set VENDOR_AGENT_USERNAME / VENDOR_AGENT_PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
