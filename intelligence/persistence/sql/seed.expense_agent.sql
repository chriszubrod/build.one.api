-- Provision expense_agent user, auth, role, and grants. Idempotent.
--
-- Mirrors seed.bill_agent.sql / seed.bill_credit_agent.sql. The
-- expense_agent's role grants Expenses CRUD+Complete and Vendors read.
--
-- The "ExpenseRefund" use case is just Expense.IsCredit=true — the
-- specialist handles both via the same grants; no separate module.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.expense_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @ExpenseModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Expenses');
DECLARE @VendorModuleId  INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Vendors');

IF @ExpenseModuleId IS NULL OR @VendorModuleId IS NULL
BEGIN
    RAISERROR('Required Module rows (Expenses / Vendors) missing. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @Username NVARCHAR(100) = 'expense_agent';
DECLARE @RoleName NVARCHAR(100) = 'Expense Specialist';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$czfZvy6MO2N7FjfmbNcJYegIlV96Yvk6iU4zpAvU.0B8fgEnNtWhO';

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'Expense');
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  expense_agent: user created (id=', @UserId, ')');
END
ELSE
    PRINT CONCAT('  expense_agent: user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  expense_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  expense_agent: auth created';
END;

DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  expense_agent: role created (id=', @RoleId, ')');
END
ELSE
    PRINT CONCAT('  expense_agent: role exists (id=', @RoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @UserId, @RoleId);
    PRINT '  expense_agent: user-role link created';
END;

-- Expenses — full CRUD + Complete.
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @ExpenseModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 1,
           CanSubmit = 0, CanApprove = 0, CanComplete = 1,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @ExpenseModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @ExpenseModuleId, 1, 1, 1, 1, 0, 0, 1);

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

PRINT '  expense_agent: module grants set (Expenses CRUD+Complete, Vendors read)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set EXPENSE_AGENT_USERNAME / EXPENSE_AGENT_PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
