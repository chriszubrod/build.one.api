-- Gap 1 — Agent UserProject backfill.
-- Grants every agent user (User.IsAgent = 1) a UserProject row for
-- every existing Project. Without this, the agent fleet (Bill /
-- Expense / Invoice / Email / etc. specialists) goes blind on the
-- Gap 1 sproc migration since agents are tenant-wide query bots.
--
-- Humans are NOT touched (per Q1.5 = a). Cassidy / Zach / Austin keep
-- their existing 1-2 UserProject assignments — those were explicit
-- admin grants and should be honored.
--
-- Idempotent: skips agents who already have a UserProject row for a
-- Project. Re-runnable as new Projects come online.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

DECLARE @inserted INT = 0;

INSERT INTO dbo.[UserProject] (CreatedDatetime, ModifiedDatetime, UserId, ProjectId, CreatedByUserId, ModifiedByUserId)
SELECT
    SYSUTCDATETIME(),
    SYSUTCDATETIME(),
    u.[Id]      AS UserId,
    p.[Id]      AS ProjectId,
    17          AS CreatedByUserId,   -- System admin (Christopher)
    17          AS ModifiedByUserId
FROM dbo.[User] u
CROSS JOIN dbo.[Project] p
WHERE u.[IsAgent] = 1
  AND NOT EXISTS (
      SELECT 1 FROM dbo.[UserProject] existing
       WHERE existing.[UserId] = u.[Id]
         AND existing.[ProjectId] = p.[Id]
  );

SET @inserted = @@ROWCOUNT;
PRINT CONCAT('Gap 1 backfill: ', @inserted, ' UserProject row(s) inserted (agents x projects).');
GO

-- Confirmation query: every agent should now have row count = total Projects.
SELECT
    u.[Firstname] + ' ' + u.[Lastname] AS Agent,
    COUNT(up.[Id]) AS UserProjectRows
FROM dbo.[User] u
LEFT JOIN dbo.[UserProject] up ON up.[UserId] = u.[Id]
WHERE u.[IsAgent] = 1
GROUP BY u.[Id], u.[Firstname], u.[Lastname]
ORDER BY u.[Id];
GO

PRINT 'Gap 1 agent UserProject backfill complete.';
