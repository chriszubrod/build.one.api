-- Configure the "Controller" role with appropriate module grants.
--
-- Idempotent — safe to re-run.
--
-- Scope: head-of-finance / accounting role.
--   - Full CRUD + approve + complete on financial ops:
--       Bills, Bill Credits, Expenses, Invoices, Contract Labor
--   - Full CRUD on reference data:
--       Vendors, Customers, Sub Cost Codes, Cost Codes, Payment Terms
--   - Read-only:
--       Projects, Companies, Organizations, Users, Roles, Dashboard,
--       Attachments, Review Statuses, Tasks, Email Messages,
--       Integrations
--   - QBO Sync: read + update (manual sync triggers)
--   - Time Tracking: read + approve
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.controller_role.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @RoleId BIGINT = (SELECT Id FROM dbo.Role WHERE Name = 'Controller');

IF @RoleId IS NULL
BEGIN
    RAISERROR('Controller role row missing.', 16, 1);
    RETURN;
END;

DECLARE @Grants TABLE (
    ModuleName NVARCHAR(100),
    CanCreate BIT, CanRead BIT, CanUpdate BIT, CanDelete BIT,
    CanSubmit BIT, CanApprove BIT, CanComplete BIT
);

-- Full financial ops (CRUD + approve + complete)
INSERT INTO @Grants VALUES
    ('Bills',          1, 1, 1, 1, 1, 1, 1),
    ('Bill Credits',   1, 1, 1, 1, 1, 1, 1),
    ('Expenses',       1, 1, 1, 1, 1, 1, 1),
    ('Invoices',       1, 1, 1, 1, 1, 1, 1),
    ('Contract Labor', 1, 1, 1, 1, 1, 1, 1);

-- Reference data CRUD
INSERT INTO @Grants VALUES
    ('Vendors',        1, 1, 1, 1, 0, 0, 0),
    ('Customers',      1, 1, 1, 1, 0, 0, 0),
    ('Sub Cost Codes', 1, 1, 1, 1, 0, 0, 0),
    ('Cost Codes',     1, 1, 1, 1, 0, 0, 0),
    ('Payment Terms',  1, 1, 1, 1, 0, 0, 0);

-- Read-only
INSERT INTO @Grants VALUES
    ('Projects',        0, 1, 0, 0, 0, 0, 0),
    ('Companies',       0, 1, 0, 0, 0, 0, 0),
    ('Organizations',   0, 1, 0, 0, 0, 0, 0),
    ('Users',           0, 1, 0, 0, 0, 0, 0),
    ('Roles',           0, 1, 0, 0, 0, 0, 0),
    ('Dashboard',       0, 1, 0, 0, 0, 0, 0),
    ('Attachments',     0, 1, 0, 0, 0, 0, 0),
    ('Review Statuses', 0, 1, 0, 0, 0, 0, 0),
    ('Tasks',           0, 1, 0, 0, 0, 0, 0),
    ('Email Messages',  0, 1, 0, 0, 0, 0, 0),
    ('Integrations',    0, 1, 0, 0, 0, 0, 0);

-- QBO Sync: read + update (manual sync triggers)
INSERT INTO @Grants VALUES
    ('QBO Sync',        0, 1, 1, 0, 0, 0, 0);

-- Time Tracking: read + approve
INSERT INTO @Grants VALUES
    ('Time Tracking',   0, 1, 0, 0, 0, 1, 0);

MERGE dbo.RoleModule AS target
USING (
    SELECT @RoleId AS RoleId, m.Id AS ModuleId,
           g.CanCreate, g.CanRead, g.CanUpdate, g.CanDelete,
           g.CanSubmit, g.CanApprove, g.CanComplete
    FROM @Grants g
    INNER JOIN dbo.[Module] m ON m.Name = g.ModuleName
) AS src
ON target.RoleId = src.RoleId AND target.ModuleId = src.ModuleId
WHEN MATCHED THEN
    UPDATE SET
        CanCreate   = src.CanCreate,
        CanRead     = src.CanRead,
        CanUpdate   = src.CanUpdate,
        CanDelete   = src.CanDelete,
        CanSubmit   = src.CanSubmit,
        CanApprove  = src.CanApprove,
        CanComplete = src.CanComplete,
        ModifiedDatetime = @Now
WHEN NOT MATCHED THEN
    INSERT (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
            CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, src.RoleId, src.ModuleId,
            src.CanCreate, src.CanRead, src.CanUpdate, src.CanDelete,
            src.CanSubmit, src.CanApprove, src.CanComplete);

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — Controller role grants merged.';
PRINT '────────────────────────────────────────────────────────────';
