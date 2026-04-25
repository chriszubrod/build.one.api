-- Provision bill_agent user, auth, role, and grants. Idempotent.
--
-- Mirrors the prior agent seeds. Notable: bill_agent's role grants
-- include CanComplete=1 so the agent's complete_bill tool can finalize
-- a draft bill (POST /api/v1/complete/bill/{public_id} requires
-- Bills can_complete). CanRead is granted on Vendors so the specialist
-- can resolve parent vendor names.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.bill_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @BillModuleId   INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Bills');
DECLARE @VendorModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Vendors');

IF @BillModuleId IS NULL OR @VendorModuleId IS NULL
BEGIN
    RAISERROR('Required Module rows (Bills / Vendors) missing. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @Username NVARCHAR(100) = 'bill_agent';
DECLARE @RoleName NVARCHAR(100) = 'Bill Specialist';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$NzPBeM9cKYUuJzbkOyP18eBZs15r0gQBaILvwwAalLSZlTYnT7pnq';

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'Bill');
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  bill_agent: user created (id=', @UserId, ')');
END
ELSE
    PRINT CONCAT('  bill_agent: user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  bill_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  bill_agent: auth created';
END;

DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  bill_agent: role created (id=', @RoleId, ')');
END
ELSE
    PRINT CONCAT('  bill_agent: role exists (id=', @RoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @UserId, @RoleId);
    PRINT '  bill_agent: user-role link created';
END;

-- Bills — full CRUD + Complete (no Create today since the agent's
-- v1 tool set deliberately omits create_bill — line items make that
-- a v2 problem — but we grant CanCreate anyway so v2 doesn't need
-- another migration).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @BillModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 1,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @BillModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @BillModuleId, 1, 1, 1, 1, 0, 0, 1);

-- Vendors — read only (for parent name resolution).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @VendorModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @VendorModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @VendorModuleId, 0, 1, 0, 0, 0, 0, 0);

PRINT '  bill_agent: module grants set (Bills CRUD+Complete, Vendors read)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set BILL_AGENT_USERNAME / BILL_AGENT_PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
