-- Patch Austin Rogers's UserProject coverage.
--
-- Two changes:
--   1. Backfill RoleId = Owner on any existing UserProject row where it's NULL
--   2. Insert UserProject rows for every Project with activity in the last 12 months
--      that Austin isn't already linked to, all carrying RoleId = Owner
--      (per project_review_notifications: PM=To, Owner=Cc).
--
-- Idempotent — safe to re-run.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/patch.austin_user_projects.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @AustinUserId BIGINT = (SELECT u.Id FROM dbo.[User] u
                                JOIN dbo.Auth a ON a.UserId=u.Id
                                WHERE a.Username='rogersar');
DECLARE @OwnerRoleId BIGINT  = (SELECT Id FROM dbo.Role WHERE Name='Owner');

IF @AustinUserId IS NULL OR @OwnerRoleId IS NULL
BEGIN
    RAISERROR('Austin or Owner role missing.', 16, 1);
    RETURN;
END;

-- ─── 1. Backfill NULL RoleId on existing rows ────────────────────────────
UPDATE dbo.UserProject
   SET RoleId = @OwnerRoleId,
       ModifiedDatetime = @Now
 WHERE UserId = @AustinUserId AND RoleId IS NULL;

PRINT CONCAT('  backfilled RoleId on ', @@ROWCOUNT, ' existing row(s)');

-- ─── 2. Insert UserProject rows for any missing active projects ──────────
DECLARE @Cutoff DATETIME2 = DATEADD(MONTH, -12, SYSUTCDATETIME());

;WITH ActiveProjectIds AS (
    SELECT DISTINCT ProjectId FROM dbo.BillLineItem          WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ExpenseLineItem       WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.Invoice               WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ContractLabor         WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.ContractLaborLineItem WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.BillCreditLineItem    WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
    UNION SELECT DISTINCT ProjectId FROM dbo.TimeLog               WHERE CreatedDatetime >= @Cutoff AND ProjectId IS NOT NULL
)
INSERT INTO dbo.UserProject (CreatedDatetime, ModifiedDatetime, UserId, ProjectId, RoleId)
SELECT @Now, @Now, @AustinUserId, a.ProjectId, @OwnerRoleId
FROM ActiveProjectIds a
INNER JOIN dbo.Project p ON p.Id = a.ProjectId
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.UserProject up
     WHERE up.UserId = @AustinUserId AND up.ProjectId = a.ProjectId
);

PRINT CONCAT('  inserted ', @@ROWCOUNT, ' new UserProject row(s)');
PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Austin UserProject coverage patched.';
PRINT '────────────────────────────────────────────────────────────';
