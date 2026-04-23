-- Cleanup: remove phantom Module rows that have no backend references and
-- no working React sidebar page. Surfaced 2026-04-23 during the Jinja-purge
-- prep; these modules correspond to features removed per CLAUDE.md
-- (anomaly, search, categorization, inbox, email_thread) or to stubs that
-- were never migrated (contacts, copilot, tasks, classification_overrides).
--
-- Run: python scripts/run_sql.py entities/module/sql/cleanup_phantom_modules.sql
--
-- Idempotent: deletes by Name match against a value list; re-running after
-- success finds no matching Module rows and is a no-op.
--
-- Gray-area modules kept intentionally (backend RBAC still gates on them,
-- even though they have no top-level React page): Attachments,
-- Pending Actions, Time Tracking. Track via TODO.md: add `is_navigable`
-- flag to Module so permission scopes can exist without sidebar entries.

SET NOCOUNT ON;

DECLARE @PhantomNames TABLE ([Name] NVARCHAR(200) PRIMARY KEY);
INSERT INTO @PhantomNames ([Name]) VALUES
    ('Anomaly Detection'),
    ('Categorization'),
    ('Classification Overrides'),
    ('Contacts'),
    ('Copilot'),
    ('Email Threads'),
    ('Inbox'),
    ('Search'),
    ('Tasks');

DECLARE @ModuleIds TABLE ([Id] BIGINT PRIMARY KEY);
INSERT INTO @ModuleIds ([Id])
SELECT m.[Id]
FROM [dbo].[Module] m
INNER JOIN @PhantomNames p ON m.[Name] = p.[Name];

DECLARE @ModuleCount INT = (SELECT COUNT(*) FROM @ModuleIds);
PRINT CONCAT('Phantom modules matched: ', @ModuleCount);

IF @ModuleCount = 0
BEGIN
    PRINT 'No phantom modules found — cleanup already applied.';
END
ELSE
BEGIN
    -- Dependent join rows first (FK_RoleModule_Module, FK_UserModule_Module)
    DECLARE @RoleModuleDeleted INT;
    DELETE rm
    FROM [dbo].[RoleModule] rm
    INNER JOIN @ModuleIds ids ON rm.[ModuleId] = ids.[Id];
    SET @RoleModuleDeleted = @@ROWCOUNT;
    PRINT CONCAT('RoleModule rows deleted: ', @RoleModuleDeleted);

    DECLARE @UserModuleDeleted INT;
    DELETE um
    FROM [dbo].[UserModule] um
    INNER JOIN @ModuleIds ids ON um.[ModuleId] = ids.[Id];
    SET @UserModuleDeleted = @@ROWCOUNT;
    PRINT CONCAT('UserModule rows deleted: ', @UserModuleDeleted);

    -- The Module rows themselves
    DECLARE @ModuleDeleted INT;
    DELETE m
    FROM [dbo].[Module] m
    INNER JOIN @ModuleIds ids ON m.[Id] = ids.[Id];
    SET @ModuleDeleted = @@ROWCOUNT;
    PRINT CONCAT('Module rows deleted: ', @ModuleDeleted);
END

GO
