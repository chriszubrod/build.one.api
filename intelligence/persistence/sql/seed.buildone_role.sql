-- Narrow buildone_agent's role to a minimal "Agent Orchestrator".
--
-- Rationale: Build.One has only delegation tools (no direct HTTP calls),
-- so it needs no module permissions. Least-privilege says zero grants.
-- Each specialist has its own role with its own narrow grants; Build.One
-- simply routes.
--
-- Run the rename migration first (scripts/migrations/rename_scout_to_buildone.sql)
-- so the Auth.Username is already 'buildone_agent' when this looks it up.
--
-- Safe to re-run. If Build.One ever grows a direct HTTP tool, add the
-- appropriate module grants to the "Agent Orchestrator" role.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.buildone_role.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @OrchestratorRoleName NVARCHAR(100) = 'Agent Orchestrator';

DECLARE @BuildOneUserId BIGINT =
    (SELECT u.Id
     FROM dbo.[User] u
     JOIN dbo.Auth a ON a.UserId = u.Id
     WHERE a.Username = 'buildone_agent');

IF @BuildOneUserId IS NULL
BEGIN
    RAISERROR('buildone_agent user not found. Run seed.intelligence_agents.sql + the rename migration first.', 16, 1);
    RETURN;
END;

-- 1. Ensure the Agent Orchestrator role exists (no module grants).
DECLARE @OrchestratorRoleId BIGINT =
    (SELECT Id FROM dbo.Role WHERE Name = @OrchestratorRoleName);

IF @OrchestratorRoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @OrchestratorRoleName);
    SET @OrchestratorRoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  created role "Agent Orchestrator" (id=', @OrchestratorRoleId, ')');
END
ELSE
BEGIN
    PRINT CONCAT('  role "Agent Orchestrator" exists (id=', @OrchestratorRoleId, ')');
END;

-- 2. Retarget Build.One's UserRole: drop existing links, add the one to Agent
--    Orchestrator. (Build.One is a single-role user in this design.)
DECLARE @PrevLinks INT = (
    SELECT COUNT(*) FROM dbo.UserRole WHERE UserId = @BuildOneUserId
);

DELETE FROM dbo.UserRole WHERE UserId = @BuildOneUserId;
PRINT CONCAT('  dropped ', @PrevLinks, ' existing UserRole link(s) for buildone_agent');

INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
VALUES (@Now, @Now, @BuildOneUserId, @OrchestratorRoleId);
PRINT '  buildone_agent user linked to Agent Orchestrator role';

-- 3. Defensive: make sure Agent Orchestrator has NO module grants. If
--    someone manually added some later, this clears them out.
DELETE FROM dbo.RoleModule WHERE RoleId = @OrchestratorRoleId;
PRINT '  ensured Agent Orchestrator has zero module grants';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Build.One now runs on a least-privilege role with no module access.';
PRINT '────────────────────────────────────────────────────────────';
