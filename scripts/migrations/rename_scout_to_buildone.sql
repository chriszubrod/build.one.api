-- Rename the orchestrator agent's login identity: scout_agent -> buildone_agent.
--
-- Part of the 2026-06-25 "Scout -> Build.One" rename. The Agent's
-- credentials_key changed from "scout_agent" to "buildone_agent", which means
-- the auth helper now reads BUILDONE_AGENT_USERNAME / BUILDONE_AGENT_PASSWORD
-- and logs in with username 'buildone_agent'. This migration renames the
-- existing Auth row so that login still resolves to the SAME User row
-- (same user_id, same UserRole/UserProject grants, same history).
--
-- The PasswordHash is deliberately left UNCHANGED — the prod
-- BUILDONE_AGENT_PASSWORD env var must carry the same cleartext that
-- SCOUT_AGENT_PASSWORD did, so the existing hash keeps verifying.
--
-- Idempotent: handles scout_agent (prod today), buildone_agent (already
-- renamed), and the legacy agent_one (very old installs).
--
-- ⚠️ PROD ORDER OF OPERATIONS (do not deviate):
--   1. Set BUILDONE_AGENT_USERNAME=buildone_agent + BUILDONE_AGENT_PASSWORD=<same as SCOUT_AGENT_PASSWORD> on App Service.
--   2. Run this migration.
--   3. Deploy the image that uses credentials_key="buildone_agent".
-- If the image deploys before this migration runs, Build.One login 500s.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py scripts/migrations/rename_scout_to_buildone.sql

SET NOCOUNT ON;
DECLARE @Now DATETIME2 = SYSUTCDATETIME();

-- Resolve which legacy username to rename. Check buildone_agent FIRST so a
-- partially-migrated DB (where buildone_agent already exists alongside a stale
-- scout_agent row) does NOT attempt a rename that would violate the unique
-- Auth.Username index.
DECLARE @OldUsername NVARCHAR(100) = NULL;
IF NOT EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = 'buildone_agent')
BEGIN
    IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = 'scout_agent')
        SET @OldUsername = 'scout_agent';
    ELSE IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = 'agent_one')
        SET @OldUsername = 'agent_one';
END;

IF @OldUsername IS NOT NULL
BEGIN
    -- Capture the user before renaming, so we can refresh the display name.
    DECLARE @UserId BIGINT =
        (SELECT UserId FROM dbo.Auth WHERE Username = @OldUsername);

    UPDATE dbo.Auth
       SET Username = 'buildone_agent',
           ModifiedDatetime = @Now
     WHERE Username = @OldUsername;
    PRINT CONCAT('  renamed Auth.Username ', @OldUsername, ' -> buildone_agent (password hash preserved)');

    IF @UserId IS NOT NULL
    BEGIN
        UPDATE dbo.[User]
           SET Firstname = 'Build.One',
               Lastname = 'Orchestrator',
               ModifiedDatetime = @Now
         WHERE Id = @UserId;
        PRINT CONCAT('  updated User id=', @UserId, ' display name -> "Build.One Orchestrator"');
    END
END
ELSE IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = 'buildone_agent')
BEGIN
    -- Already renamed; just make sure the display name is consistent.
    DECLARE @ExistingUserId BIGINT =
        (SELECT UserId FROM dbo.Auth WHERE Username = 'buildone_agent');
    UPDATE dbo.[User]
       SET Firstname = 'Build.One',
           Lastname = 'Orchestrator',
           ModifiedDatetime = @Now
     WHERE Id = @ExistingUserId;
    PRINT '  buildone_agent already present — display name reaffirmed (no-op rename)';
END
ELSE
BEGIN
    PRINT '  NONE of agent_one / scout_agent / buildone_agent found — nothing to rename';
END;

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — re-run seed.buildone_role.sql afterward if the role link needs reasserting.';
PRINT '────────────────────────────────────────────────────────────';
