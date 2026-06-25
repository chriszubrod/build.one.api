-- Provision email_agent user, auth, role, and grants. Idempotent.
--
-- The email agent is a pure orchestrator (like buildone) — it reads
-- polled emails, decides what each is, and delegates to bill_specialist
-- (and later expense/bill_credit specialists) for actual entity work.
-- It carries grants ONLY on the Email Messages module so it can:
--   - read EmailMessage rows (read tools)
--   - update them (mark_email_outcome flips ProcessingStatus)
-- Vendors / Bills / Expenses / Bill Credits / Cost Codes / Projects
-- are intentionally NOT granted — those go through the specialists,
-- which carry their own narrower grants.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.email_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @EmailModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Email Messages');

IF @EmailModuleId IS NULL
BEGIN
    RAISERROR('Required Module row (Email Messages) missing. Run seed.EmailMessagesModule.sql first.', 16, 1);
    RETURN;
END;

DECLARE @Username NVARCHAR(100) = 'email_agent';
DECLARE @RoleName NVARCHAR(100) = 'Email Specialist';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$IUou4uG3uJybN7yUDHl3kOhYWqluZT1RulLG.0a7bQ3Z/eIBiNZT6';

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname)
    VALUES (@Now, @Now, 'Agent', 'Email');
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  email_agent: user created (id=', @UserId, ')');
END
ELSE
    PRINT CONCAT('  email_agent: user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  email_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  email_agent: auth created';
END;

DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  email_agent: role created (id=', @RoleId, ')');
END
ELSE
    PRINT CONCAT('  email_agent: role exists (id=', @RoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
    VALUES (@Now, @Now, @UserId, @RoleId);
    PRINT '  email_agent: user-role link created';
END;

-- Email Messages — read + update. No create/delete/etc.
-- Reads: agent loads pending emails, lists attachments.
-- Update: mark_email_outcome flips ProcessingStatus + records LastError.
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @EmailModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 1, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @EmailModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @EmailModuleId, 0, 1, 1, 0, 0, 0, 0);

PRINT '  email_agent: module grants set (Email Messages read+update)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set EMAIL_AGENT_USERNAME / EMAIL_AGENT_PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
