-- Narrow scout_agent's role from Admin to a minimal "Agent Orchestrator".
--
-- Rationale: scout has only delegation tools now (no direct HTTP calls),
-- so it needs no module permissions. Admin is overkill; least-privilege
-- says zero grants. Each specialist has its own role with its own narrow
-- grants; scout simply routes.
--
-- Safe to re-run. If scout ever grows a direct HTTP tool, add the
-- appropriate module grants to the "Agent Orchestrator" role.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.scout_role.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @OrchestratorRoleName NVARCHAR(100) = 'Agent Orchestrator';

DECLARE @ScoutUserId BIGINT =
    (SELECT u.Id
     FROM dbo.[User] u
     JOIN dbo.Auth a ON a.UserId = u.Id
     WHERE a.Username = 'scout_agent');

IF @ScoutUserId IS NULL
BEGIN
    RAISERROR('scout_agent user not found. Run seed.intelligence_agents.sql first.', 16, 1);
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

-- 2. Retarget scout's UserRole: drop existing links, add the one to Agent
--    Orchestrator. (Scout is a single-role user in this design; if we
--    ever needed multi-role, we'd only drop Admin specifically.)
DECLARE @PrevLinks INT = (
    SELECT COUNT(*) FROM dbo.UserRole WHERE UserId = @ScoutUserId
);

DELETE FROM dbo.UserRole WHERE UserId = @ScoutUserId;
PRINT CONCAT('  dropped ', @PrevLinks, ' existing UserRole link(s) for scout');

INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId)
VALUES (@Now, @Now, @ScoutUserId, @OrchestratorRoleId);
PRINT '  scout user linked to Agent Orchestrator role';

-- 3. Defensive: make sure Agent Orchestrator has NO module grants. If
--    someone manually added some later, this clears them out.
DELETE FROM dbo.RoleModule WHERE RoleId = @OrchestratorRoleId;
PRINT '  ensured Agent Orchestrator has zero module grants';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — scout now runs on a least-privilege role with no module access.';
PRINT '────────────────────────────────────────────────────────────';
