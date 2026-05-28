-- Seed the 'Intern' human Role + its RoleModule grants.
--
-- The Intern is the iOS on-device user who logs their OWN time
-- against projects. Distinct from 'Time Clerk' (back-office time entry /
-- correction). Phase 3 row-scoping limits an Intern to their own
-- TimeEntry rows; submit/approve/reject ride on Time Tracking can_update
-- (the router does NOT read CanSubmit/CanApprove), so in practice this is
-- self-submit only.
--
-- Grant matrix (C/R/U/D/S/A/Cmp):
--   Time Tracking  C R U . . . .   (operational; no delete per sign-off)
--   Projects       . R . . . . .   (iOS project picker)
--   Users          . R U . . . .   (own-profile read + edit from iOS;
--                                    NOTE: Users can_update is NOT row-scoped)
--   Companies      . R . . . . .   (company switcher)
--   Organizations  . R . . . . .   (company switcher)
--   Roles          . R . . . . .   (iOS reads /get/roles, /get/modules)
--
-- Idempotent (IF NOT EXISTS guard on Role; MERGE on RoleModule). Safe to re-run.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py entities/role/sql/migrations/004_seed_field_worker_role.sql

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

-- ---------------------------------------------------------------------
-- 1. Role row
-- ---------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM dbo.[Role] WHERE [Name] = N'Intern')
BEGIN
    INSERT INTO dbo.[Role] ([CreatedDatetime], [ModifiedDatetime], [Name])
    VALUES (@Now, @Now, N'Intern');
    PRINT 'Seeded Role: Intern';
END
ELSE
BEGIN
    PRINT 'Role Intern already exists - skipping insert.';
END

DECLARE @InternId BIGINT = (SELECT [Id] FROM dbo.[Role] WHERE [Name] = N'Intern');

-- ---------------------------------------------------------------------
-- 2. RoleModule grants
--    (RoleId, ModuleName, CanCreate, CanRead, CanUpdate, CanDelete,
--     CanSubmit, CanApprove, CanComplete)
-- ---------------------------------------------------------------------
DECLARE @Grants TABLE (
    [ModuleName] NVARCHAR(100),
    [CanCreate]  BIT,
    [CanRead]    BIT,
    [CanUpdate]  BIT,
    [CanDelete]  BIT,
    [CanSubmit]  BIT,
    [CanApprove] BIT,
    [CanComplete] BIT
);

INSERT INTO @Grants ([ModuleName], [CanCreate], [CanRead], [CanUpdate], [CanDelete], [CanSubmit], [CanApprove], [CanComplete])
VALUES
    (N'Time Tracking', 1, 1, 1, 0, 0, 0, 0),
    (N'Projects',      0, 1, 0, 0, 0, 0, 0),
    (N'Users',         0, 1, 1, 0, 0, 0, 0),
    (N'Companies',     0, 1, 0, 0, 0, 0, 0),
    (N'Organizations', 0, 1, 0, 0, 0, 0, 0),
    (N'Roles',         0, 1, 0, 0, 0, 0, 0);

-- Pre-flight: every named Module must exist.
IF EXISTS (
    SELECT 1 FROM @Grants g
    WHERE NOT EXISTS (SELECT 1 FROM dbo.[Module] m WHERE m.[Name] = g.[ModuleName])
)
BEGIN
    DECLARE @Missing NVARCHAR(1000) = (
        SELECT STRING_AGG(g.[ModuleName], ', ')
        FROM @Grants g
        WHERE NOT EXISTS (SELECT 1 FROM dbo.[Module] m WHERE m.[Name] = g.[ModuleName])
    );
    RAISERROR('Missing Module rows: %s - run entities/module/sql/seed.AllModules.sql first.', 16, 1, @Missing);
    RETURN;
END;

MERGE dbo.[RoleModule] AS target
USING (
    SELECT
        @InternId AS RoleId,
        m.[Id]         AS ModuleId,
        g.[CanCreate], g.[CanRead], g.[CanUpdate], g.[CanDelete],
        g.[CanSubmit], g.[CanApprove], g.[CanComplete]
    FROM @Grants g
    INNER JOIN dbo.[Module] m ON m.[Name] = g.[ModuleName]
) AS src
ON target.[RoleId] = src.RoleId AND target.[ModuleId] = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET
        [CanCreate]   = src.[CanCreate],
        [CanRead]     = src.[CanRead],
        [CanUpdate]   = src.[CanUpdate],
        [CanDelete]   = src.[CanDelete],
        [CanSubmit]   = src.[CanSubmit],
        [CanApprove]  = src.[CanApprove],
        [CanComplete] = src.[CanComplete],
        [ModifiedDatetime] = @Now
WHEN NOT MATCHED THEN
    INSERT ([CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
            [CanCreate], [CanRead], [CanUpdate], [CanDelete],
            [CanSubmit], [CanApprove], [CanComplete])
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            src.[CanCreate], src.[CanRead], src.[CanUpdate], src.[CanDelete],
            src.[CanSubmit], src.[CanApprove], src.[CanComplete]);

PRINT CONCAT('Intern RoleModule grants merged: ', @@ROWCOUNT, ' rows.');
PRINT 'Done - Intern role seeded.';
