-- Limit Tanner Baker's Project access to: OHR2 projects, BR projects, and SHT.
--
-- Removes UserProject rows that don't match. Keeps RoleId on the survivors.
-- Idempotent — safe to re-run.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/patch.tanner_scope_projects.sql

DECLARE @TannerUserId BIGINT = (SELECT u.Id FROM dbo.[User] u
                                JOIN dbo.Auth a ON a.UserId = u.Id
                                WHERE a.Username = 'bakertb');

IF @TannerUserId IS NULL
BEGIN
    RAISERROR('Tanner user row missing.', 16, 1);
    RETURN;
END;

-- Resolve keeper ProjectIds via name prefix match
DECLARE @Keepers TABLE (ProjectId BIGINT PRIMARY KEY);
INSERT INTO @Keepers
SELECT Id FROM dbo.Project
WHERE Name LIKE 'OHR2 -%' OR Name LIKE 'OHR2-%'
   OR Name LIKE 'BR-%'                       -- Buffalo Road only (not "BR - Brattlesboro")
   OR Name LIKE 'SHT -%'  OR Name LIKE 'SHT-%';

DECLARE @KeeperCount INT = (SELECT COUNT(*) FROM @Keepers);
PRINT CONCAT('  keeper projects matched by name: ', @KeeperCount);

DELETE FROM dbo.UserProject
 WHERE UserId = @TannerUserId
   AND ProjectId NOT IN (SELECT ProjectId FROM @Keepers);

PRINT CONCAT('  removed ', @@ROWCOUNT, ' non-keeper UserProject row(s)');

DECLARE @Remaining INT = (SELECT COUNT(*) FROM dbo.UserProject WHERE UserId = @TannerUserId);
PRINT CONCAT('  remaining UserProject rows for Tanner: ', @Remaining);
PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Tanner project scope limited.';
PRINT '────────────────────────────────────────────────────────────';
