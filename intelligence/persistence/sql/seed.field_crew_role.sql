-- Provision the "Field Crew" Role + RoleModule grants.
--
-- Used by field workers (iOS Time Tracking + React Profile page).
-- Idempotent — safe to re-run.
--
-- Grants (minimum needed for iOS time tracking + functional Profile page):
--   Time Tracking : read, create, update
--   Dashboard     : read           (for /api/v1/lookups dropdown data)
--   Users         : read           (own profile basics)
--   Roles         : read           (own role/module sections on Profile)
--   Projects      : read           (own UserProject section on Profile)
--   Vendors       : read           (Contact router is gated on Vendors;
--                                   needed for the Contacts section)
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.field_crew_role.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @RoleName NVARCHAR(100) = 'Field Crew';

-- ─── 1. Role row ─────────────────────────────────────────────────────────
DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  role created (id=', @RoleId, ')');
END
ELSE
BEGIN
    PRINT CONCAT('  role exists (id=', @RoleId, ')');
END;

-- ─── 2. RoleModule grants ───────────────────────────────────────────────
DECLARE @TimeTrackingId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Time Tracking');
DECLARE @DashboardId    INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Dashboard');
DECLARE @UsersId        INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Users');
DECLARE @RolesId        INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Roles');
DECLARE @ProjectsId     INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Projects');
DECLARE @VendorsId      INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Vendors');

IF @TimeTrackingId IS NULL OR @DashboardId IS NULL OR @UsersId IS NULL
   OR @RolesId IS NULL OR @ProjectsId IS NULL OR @VendorsId IS NULL
BEGIN
    RAISERROR('Required Module rows missing. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @Grants TABLE (
    ModuleId INT,
    CanCreate BIT,
    CanRead BIT,
    CanUpdate BIT,
    CanDelete BIT,
    CanSubmit BIT,
    CanApprove BIT,
    CanComplete BIT
);

INSERT INTO @Grants VALUES
    (@TimeTrackingId, 1, 1, 1, 0, 0, 0, 0),
    (@DashboardId,    0, 1, 0, 0, 0, 0, 0),
    (@UsersId,        0, 1, 0, 0, 0, 0, 0),
    (@RolesId,        0, 1, 0, 0, 0, 0, 0),
    (@ProjectsId,     0, 1, 0, 0, 0, 0, 0),
    (@VendorsId,      0, 1, 0, 0, 0, 0, 0);

MERGE dbo.RoleModule AS target
USING (
    SELECT @RoleId AS RoleId, ModuleId, CanCreate, CanRead, CanUpdate,
           CanDelete, CanSubmit, CanApprove, CanComplete
    FROM @Grants
) AS src
ON target.RoleId = src.RoleId AND target.ModuleId = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET
        CanCreate = src.CanCreate,
        CanRead = src.CanRead,
        CanUpdate = src.CanUpdate,
        CanDelete = src.CanDelete,
        CanSubmit = src.CanSubmit,
        CanApprove = src.CanApprove,
        CanComplete = src.CanComplete,
        ModifiedDatetime = @Now
WHEN NOT MATCHED THEN
    INSERT (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
            CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            src.CanCreate, src.CanRead, src.CanUpdate, src.CanDelete,
            src.CanSubmit, src.CanApprove, src.CanComplete);

PRINT '  module grants merged (6 modules)';
PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Field Crew role provisioned.';
PRINT '────────────────────────────────────────────────────────────';
