-- Provision invoice_agent user, auth, role, and grants. Idempotent.
--
-- Mirrors prior agent seeds. Notable: parent for Invoice is Project
-- (not Vendor), so the role grants Invoices CRUD+Complete and
-- Projects read.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.invoice_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @InvoiceModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Invoices');
DECLARE @ProjectModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Projects');

IF @InvoiceModuleId IS NULL OR @ProjectModuleId IS NULL
BEGIN
    RAISERROR('Required Module rows (Invoices / Projects) missing. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @Username NVARCHAR(100) = 'invoice_agent';
DECLARE @RoleName NVARCHAR(100) = 'Invoice Specialist';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$/GnkFkqM.SRgP7i2DEUss./4xvgHHNrhzm6csPz0/d7UOlsI1JbUm';

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'Invoice');
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  invoice_agent: user created (id=', @UserId, ')');
END
ELSE
    PRINT CONCAT('  invoice_agent: user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  invoice_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  invoice_agent: auth created';
END;

DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  invoice_agent: role created (id=', @RoleId, ')');
END
ELSE
    PRINT CONCAT('  invoice_agent: role exists (id=', @RoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @UserId, @RoleId);
    PRINT '  invoice_agent: user-role link created';
END;

-- Invoices — full CRUD + Complete.
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @InvoiceModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 1,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @InvoiceModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @InvoiceModuleId, 1, 1, 1, 1, 0, 0, 1);

-- Projects — read only (for parent name resolution).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @ProjectModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @ProjectModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @ProjectModuleId, 0, 1, 0, 0, 0, 0, 0);

PRINT '  invoice_agent: module grants set (Invoices CRUD+Complete, Projects read)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set INVOICE_AGENT_USERNAME / INVOICE_AGENT_PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
