-- Provision time_tracking_agent user, auth, role, and grants. Idempotent.
--
-- The time_tracking_specialist agent reviews iOS-submitted TimeEntries
-- (status flip draft -> submitted), validates completeness, and stamps
-- ReviewPriority + ReviewReasons for the human Approver. Flag-only v1 —
-- never transitions CurrentStatus.
--
-- Grants:
--   Time Tracking — Create + Read + Update + ViewTeam (no Delete/Submit/Approve/Complete).
--                   `validate_time_entry_completeness` needs can_read;
--                   `flag_time_entry_for_human_review` needs can_update.
--                   CanCreate=1 is a forward-compat hedge — v1 doesn't use it,
--                   but matches the bill_agent shape so v2 doesn't need a
--                   migration if the agent ever needs to create a TimeEntry.
--                   CanViewTeam=1 lets a time-tracking agent SEE other workers'
--                   entries via the row-scoped LIST endpoint (it owns none of
--                   its own). Inert for THIS API agent (it reads by public_id
--                   with a system-admin bypass), but required by the MCP/Cowork
--                   agency agent, which stacks this role onto claude_agent and
--                   discovers crew drafts through that scoped list.
--   Projects      — Read only (lookup Project name for context enrichment).
--   Users         — Read only (lookup the submitter's name for context).
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.time_tracking_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @TimeTrackingModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Time Tracking');
DECLARE @ProjectModuleId      INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Projects');
DECLARE @UserModuleId         INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Users');
DECLARE @RogersCompanyId      BIGINT = (SELECT Id FROM dbo.Company WHERE Name = 'Rogers Build, Inc.');

IF @TimeTrackingModuleId IS NULL OR @ProjectModuleId IS NULL OR @UserModuleId IS NULL OR @RogersCompanyId IS NULL
BEGIN
    RAISERROR('Required Module/Company rows (Time Tracking / Projects / Users / Rogers Build) missing. Aborting.', 16, 1);
    RETURN;
END;

-- The Time Tracking grant below writes RoleModule.CanViewTeam (added by
-- scripts/migrations/time_entry_view_team.sql). Fail fast with an actionable
-- message instead of an opaque "Invalid column name" mid-script if the
-- migration hasn't been applied to this database yet.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanViewTeam'
)
BEGIN
    RAISERROR('dbo.RoleModule.CanViewTeam missing — run scripts/migrations/time_entry_view_team.sql first. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @Username NVARCHAR(100) = 'time_tracking_agent';
DECLARE @RoleName NVARCHAR(100) = 'Time Tracking Specialist';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$/hF.lu0dvoubIehvNPCPG.cEoLRa26RTQBmqmE/rTk528Kb4bRbR6';

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname, IsAgent)
    VALUES (@Now, @Now, 'Agent', 'Time Tracking', 1);
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  time_tracking_agent: user created (id=', @UserId, ')');
END
ELSE
    PRINT CONCAT('  time_tracking_agent: user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  time_tracking_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  time_tracking_agent: auth created';
END;

DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  time_tracking_agent: role created (id=', @RoleId, ')');
END
ELSE
    PRINT CONCAT('  time_tracking_agent: role exists (id=', @RoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId AND CompanyId = @RogersCompanyId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
    VALUES (@Now, @Now, @UserId, @RoleId, @RogersCompanyId);
    PRINT '  time_tracking_agent: user-role link created (Rogers Build, Inc.)';
END;

IF NOT EXISTS (SELECT 1 FROM dbo.UserCompany WHERE UserId = @UserId AND CompanyId = @RogersCompanyId)
BEGIN
    INSERT INTO dbo.UserCompany (CreatedDatetime, ModifiedDatetime, UserId, CompanyId)
    VALUES (@Now, @Now, @UserId, @RogersCompanyId);
    PRINT '  time_tracking_agent: user-company link created (Rogers Build, Inc.)';
END;

-- Time Tracking — Create + Read + Update + ViewTeam (no Delete/Submit/Approve/Complete).
-- CanViewTeam=1: see the header note. Column added by
-- scripts/migrations/time_entry_view_team.sql (must be applied first).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @TimeTrackingModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0, CanViewTeam = 1,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @TimeTrackingModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete, CanViewTeam)
    VALUES (@Now, @Now, @RoleId, @TimeTrackingModuleId, 1, 1, 1, 0, 0, 0, 0, 1);

-- Projects — read only (Project lookup for context).
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

-- Users — read only (submitter name for context).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @UserModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @UserModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @UserModuleId, 0, 1, 0, 0, 0, 0, 0);

PRINT '  time_tracking_agent: module grants set (Time Tracking CRU, Projects read, Users read)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set time_tracking_agent_username / time_tracking_agent_password on App Service.';
PRINT '────────────────────────────────────────────────────────────';
