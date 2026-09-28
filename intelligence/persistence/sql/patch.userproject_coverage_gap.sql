-- U-556 — UserProject coverage gap for projects created after the one-off backfills.
--
-- WHY THIS EXISTS
-- ---------------
-- Two one-off scripts populated dbo.UserProject and were never re-run:
--   * scripts/migrations/gap1_agent_user_project_backfill.sql   (agents x projects)
--   * intelligence/persistence/sql/patch.austin_user_projects.sql (Austin = Owner)
-- Both only covered Projects that existed on the day they ran, so every Project
-- created afterwards starts with ZERO coverage. Verified live 2026-09-28: projects
-- 201, 202, 203, 204 had no agent rows at all, and Austin was absent from 7.
--
-- This is NOT cosmetic. Non-sysadmin agent users authenticate as themselves
-- (intelligence/run.py:59 logs each agent in and carries its own JWT), so
-- shared/access.py::_should_bypass() — which keys on IsSystemAdmin ALONE — is
-- False for the whole fleet. Every route that calls assert_can_access_* then
-- runs dbo.UserCanAccess{Project,Bill,BillCredit,Expense,TimeEntry}, which
-- resolve through dbo.UserProject. A missing row silently narrows what an agent
-- can read on a new project. Only claude_agent (IsSystemAdmin=1) bypasses.
--
-- Idempotent — WHERE NOT EXISTS on every insert, safe to re-run. The UNIQUE
-- index UQ_UserProject_UserId_ProjectId makes a duplicate fail loud rather than
-- silently double-granting (it was added after the 2026-05-27 mass-backfill
-- produced exactly that).
--
-- RUN:
--   ./.venv/bin/python scripts/run_sql.py intelligence/persistence/sql/patch.userproject_coverage_gap.sql

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ─── 0. Pre-flight: report the gap before changing anything ──────────────
DECLARE @AgentGap INT = (
    SELECT COUNT(*) FROM dbo.[User] u CROSS JOIN dbo.[Project] p
     WHERE u.[IsAgent] = 1
       AND NOT EXISTS (SELECT 1 FROM dbo.[UserProject] e
                        WHERE e.[UserId] = u.[Id] AND e.[ProjectId] = p.[Id])
);
DECLARE @OwnerGap INT = (
    SELECT COUNT(*) FROM dbo.[Project] p
     WHERE NOT EXISTS (SELECT 1 FROM dbo.[UserProject] up
                        WHERE up.[UserId] = (SELECT u.[Id] FROM dbo.[User] u
                                              JOIN dbo.[Auth] a ON a.[UserId] = u.[Id]
                                             WHERE a.[Username] = 'rogersar')
                          AND up.[ProjectId] = p.[Id])
);
PRINT CONCAT('PRE-FLIGHT: agent rows missing = ', @AgentGap, ' | Owner rows missing = ', @OwnerGap);
GO

-- ─── 1. Agents x Projects ────────────────────────────────────────────────
-- Reuses gap1_agent_user_project_backfill.sql's exact CROSS JOIN + NOT EXISTS
-- shape rather than inventing a second pattern. That script already documents
-- the intent ("agents are tenant-wide query bots") and declares itself
-- "re-runnable as new Projects come online" — it simply was never re-run.
--
-- NOTE ON VOLUME: contract_labor_agent (49) and time_tracking_agent (50) were
-- provisioned AFTER gap1 ran and carry ZERO rows, so they account for the bulk
-- of this insert. That is a genuine access expansion, not a no-op top-up, and
-- is called out in the unit notes. Both are covered by gap1's stated intent.
DECLARE @AgentInserted INT = 0;

INSERT INTO dbo.[UserProject] (CreatedDatetime, ModifiedDatetime, UserId, ProjectId, CreatedByUserId, ModifiedByUserId)
SELECT SYSUTCDATETIME(), SYSUTCDATETIME(), u.[Id], p.[Id], 17, 17
FROM dbo.[User] u
CROSS JOIN dbo.[Project] p
WHERE u.[IsAgent] = 1
  AND NOT EXISTS (
      SELECT 1 FROM dbo.[UserProject] existing
       WHERE existing.[UserId] = u.[Id]
         AND existing.[ProjectId] = p.[Id]
  );

SET @AgentInserted = @@ROWCOUNT;
PRINT CONCAT('  agents:  inserted ', @AgentInserted, ' UserProject row(s)');
GO

-- ─── 2. Austin Rogers = Owner on every Project ───────────────────────────
-- patch.austin_user_projects.sql restricted itself to projects with activity in
-- the last 12 months. That filter is wrong for review routing specifically: the
-- Owner Cc has to be in place BEFORE the project's first bill arrives, and a
-- brand-new project has no activity by definition. Verified 2026-09-28 —
-- projects 202 and 204 carry zero activity rows and would stay uncovered under
-- the old filter, which is exactly how bill 40638 on project 203 ended up with
-- an empty Cc line.
--
-- RoleId = Owner is load-bearing, not decorative: entities/review/business/
-- recipient_service.py buckets 'Project Manager' -> To and 'Owner' -> Cc. A NULL
-- RoleId drops the user from review-notification routing entirely.
DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
DECLARE @AustinUserId BIGINT = (SELECT u.[Id] FROM dbo.[User] u
                                  JOIN dbo.[Auth] a ON a.[UserId] = u.[Id]
                                 WHERE a.[Username] = 'rogersar');
DECLARE @OwnerRoleId BIGINT = (SELECT [Id] FROM dbo.[Role] WHERE [Name] = 'Owner');

IF @AustinUserId IS NULL OR @OwnerRoleId IS NULL
BEGIN
    RAISERROR('Austin user row or Owner role missing — refusing to continue.', 16, 1);
    RETURN;
END;

-- 2a. Backfill RoleId on any existing Austin row that lost it.
UPDATE dbo.[UserProject]
   SET [RoleId] = @OwnerRoleId,
       [ModifiedDatetime] = @Now
 WHERE [UserId] = @AustinUserId AND [RoleId] IS NULL;
PRINT CONCAT('  owner:   backfilled RoleId on ', @@ROWCOUNT, ' existing row(s)');

-- 2b. Insert the missing ones.
INSERT INTO dbo.[UserProject] (CreatedDatetime, ModifiedDatetime, UserId, ProjectId, RoleId, CreatedByUserId, ModifiedByUserId)
SELECT @Now, @Now, @AustinUserId, p.[Id], @OwnerRoleId, 17, 17
FROM dbo.[Project] p
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.[UserProject] up
     WHERE up.[UserId] = @AustinUserId AND up.[ProjectId] = p.[Id]
);
PRINT CONCAT('  owner:   inserted ', @@ROWCOUNT, ' UserProject row(s)');
GO

-- ─── 3. Post-flight verification ─────────────────────────────────────────
DECLARE @Projects INT = (SELECT COUNT(*) FROM dbo.[Project]);

PRINT '────────────────────────────────────────────────────────────';
PRINT CONCAT('Total projects: ', @Projects);

-- Every agent should now equal @Projects. Any shortfall is a real failure.
IF EXISTS (
    SELECT 1 FROM dbo.[User] u
     WHERE u.[IsAgent] = 1
       AND (SELECT COUNT(*) FROM dbo.[UserProject] up WHERE up.[UserId] = u.[Id]) <> @Projects
)
BEGIN
    PRINT 'FAIL: at least one agent still has incomplete project coverage:';
    SELECT u.[Id],
           u.[Firstname] + ' ' + u.[Lastname] AS Agent,
           (SELECT COUNT(*) FROM dbo.[UserProject] up WHERE up.[UserId] = u.[Id]) AS Rows_,
           @Projects AS Expected
      FROM dbo.[User] u
     WHERE u.[IsAgent] = 1
       AND (SELECT COUNT(*) FROM dbo.[UserProject] up WHERE up.[UserId] = u.[Id]) <> @Projects
     ORDER BY u.[Id];
END
ELSE
    PRINT 'OK: every agent covers every project.';

-- Austin must cover every project AND carry RoleId = Owner on all of them.
DECLARE @AustinId BIGINT = (SELECT u.[Id] FROM dbo.[User] u
                              JOIN dbo.[Auth] a ON a.[UserId] = u.[Id]
                             WHERE a.[Username] = 'rogersar');
DECLARE @AustinRows INT = (SELECT COUNT(*) FROM dbo.[UserProject] WHERE [UserId] = @AustinId);
DECLARE @AustinNullRole INT = (SELECT COUNT(*) FROM dbo.[UserProject]
                                WHERE [UserId] = @AustinId AND [RoleId] IS NULL);

IF @AustinRows = @Projects AND @AustinNullRole = 0
    PRINT CONCAT('OK: Austin covers all ', @Projects, ' projects, all tagged Owner.');
ELSE
    PRINT CONCAT('FAIL: Austin rows=', @AustinRows, ' expected=', @Projects,
                 ' | NULL RoleId rows=', @AustinNullRole);

PRINT '────────────────────────────────────────────────────────────';
PRINT 'U-556 UserProject coverage patch complete.';
GO
