-- =============================================================================
-- 2026-05-27 — Phase 1 backfill: link existing Users to Employee / Vendor rows.
--
-- Six current workers are 1099 contract-labor Vendors and get User.VendorId
-- pointing at their existing Vendor row. One current worker (Selvin Cordova)
-- is a W2 Employee — gets a NEW Employee row seeded from his old VENDOR_CONFIG
-- entry ($500/hr, 0.35 markup) + User.EmployeeId pointing at it.
--
-- Selvin's old "Selvin Humberto Cordova Tercero" Vendor row is LEFT IN PLACE
-- so historical ContractLabor → Bill records still resolve. New time entries
-- will flow through the Employee path (Phase 3+).
--
-- Idempotent — each step checks for existing state before updating. Safe to
-- re-run. PRINT statements report each step's outcome.
--
-- Run via:
--   python scripts/run_sql.py scripts/backfill_user_worker_links.sql
--
-- Prereq: Phase 1 schema applied
--   - entities/employee/sql/dbo.employee.sql
--   - entities/user/sql/dbo.user.sql
--   - entities/user/sql/migrations/005_2026_05_27_worker_links.sql
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- ─────────────────────────────────────────────────────────────────────
-- Build the worker manifest as a table variable. Each row describes
-- one User → Worker mapping. NameLikePattern matches against
-- (Firstname + ' ' + Lastname) — defensive against varying name
-- formats in dbo.[User].
-- ─────────────────────────────────────────────────────────────────────

DECLARE @Workers TABLE (
    NameLikePattern  NVARCHAR(255) NOT NULL,
    WorkerType       NVARCHAR(20)  NOT NULL,   -- 'employee' or 'vendor'
    VendorName       NVARCHAR(450) NULL,        -- when WorkerType='vendor', the dbo.Vendor.Name to link
    EmpFirstname     NVARCHAR(50)  NULL,        -- when WorkerType='employee', for new Employee row
    EmpLastname      NVARCHAR(255) NULL,
    EmpHourlyRate    DECIMAL(18,4) NULL,
    EmpMarkup        DECIMAL(18,4) NULL
);

-- The one Employee
INSERT INTO @Workers VALUES
    ('%Selvin%Cordova%',  'employee', NULL, 'Selvin', 'Cordova', 500.00, 0.3500);

-- The six 1099 contract-labor Vendors. NameLikePattern matches the User row;
-- VendorName is the exact Vendor.Name (from VENDOR_CONFIG keys today).
INSERT INTO @Workers VALUES
    ('%Denis%Samuel%',    'vendor',   N'Denis Samuel Marcia Izaguirre', NULL, NULL, NULL, NULL),
    ('%Wilmer%Diaz%',     'vendor',   N'Wilmer Diaz',                   NULL, NULL, NULL, NULL),
    ('%Elmer%Cordova%',   'vendor',   N'Elmer Cordova',                 NULL, NULL, NULL, NULL),
    ('%Emilson%Cordova%', 'vendor',   N'Emilson O. Cordova Tercero',    NULL, NULL, NULL, NULL),
    ('%Michael%Jacobson%','vendor',   N'Michael Jacobson',              NULL, NULL, NULL, NULL),
    ('%Brayan%Rafael%',   'vendor',   N'Brayan Rafael Marcia Salina',   NULL, NULL, NULL, NULL);


-- ─────────────────────────────────────────────────────────────────────
-- Cursor through each worker manifest row and apply the link.
-- ─────────────────────────────────────────────────────────────────────

DECLARE @NameLikePattern NVARCHAR(255);
DECLARE @WorkerType      NVARCHAR(20);
DECLARE @VendorName      NVARCHAR(450);
DECLARE @EmpFirstname    NVARCHAR(50);
DECLARE @EmpLastname     NVARCHAR(255);
DECLARE @EmpHourlyRate   DECIMAL(18,4);
DECLARE @EmpMarkup       DECIMAL(18,4);

DECLARE worker_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT NameLikePattern, WorkerType, VendorName, EmpFirstname, EmpLastname, EmpHourlyRate, EmpMarkup
    FROM @Workers;

OPEN worker_cursor;
FETCH NEXT FROM worker_cursor INTO
    @NameLikePattern, @WorkerType, @VendorName, @EmpFirstname, @EmpLastname, @EmpHourlyRate, @EmpMarkup;

WHILE @@FETCH_STATUS = 0
BEGIN
    DECLARE @UserId          BIGINT = NULL;
    DECLARE @UserMatchCount  INT;
    DECLARE @UserDisplayName NVARCHAR(310);

    -- Resolve the User row by name-pattern match. Combine Firstname + Lastname
    -- for matching so 'Selvin Cordova' / 'Selvin Humberto Cordova Tercero' /
    -- 'Selvin' / 'Cordova' all resolve.
    SELECT @UserMatchCount = COUNT(*)
    FROM dbo.[User] u
    WHERE (ISNULL(u.[Firstname], '') + ' ' + ISNULL(u.[Lastname], '')) LIKE @NameLikePattern;

    IF @UserMatchCount = 0
    BEGIN
        PRINT '  SKIP: no User matches pattern ' + @NameLikePattern;
        GOTO NextRow;
    END

    IF @UserMatchCount > 1
    BEGIN
        PRINT '  SKIP: ' + CAST(@UserMatchCount AS NVARCHAR(10)) + ' Users match pattern ' + @NameLikePattern + ' — tighten the pattern and re-run.';
        GOTO NextRow;
    END

    SELECT TOP 1
        @UserId          = u.[Id],
        @UserDisplayName = ISNULL(u.[Firstname], '') + ' ' + ISNULL(u.[Lastname], '')
    FROM dbo.[User] u
    WHERE (ISNULL(u.[Firstname], '') + ' ' + ISNULL(u.[Lastname], '')) LIKE @NameLikePattern;

    IF @WorkerType = 'employee'
    BEGIN
        -- INSERT Employee row if it doesn't already exist
        DECLARE @EmployeeId BIGINT;
        SELECT @EmployeeId = [Id]
        FROM dbo.[Employee]
        WHERE [Firstname] = @EmpFirstname AND [Lastname] = @EmpLastname AND [IsDeleted] = 0;

        IF @EmployeeId IS NULL
        BEGIN
            INSERT INTO dbo.[Employee]
                ([CreatedDatetime], [ModifiedDatetime], [Firstname], [Lastname], [HourlyRate], [Markup], [IsActive])
            VALUES
                (SYSUTCDATETIME(), SYSUTCDATETIME(), @EmpFirstname, @EmpLastname, @EmpHourlyRate, @EmpMarkup, 1);
            SET @EmployeeId = SCOPE_IDENTITY();
            PRINT '  CREATED: Employee row id=' + CAST(@EmployeeId AS NVARCHAR(20)) + ' for ' + @EmpFirstname + ' ' + @EmpLastname + ' (rate=' + CAST(@EmpHourlyRate AS NVARCHAR(20)) + ', markup=' + CAST(@EmpMarkup AS NVARCHAR(20)) + ')';
        END
        ELSE
        BEGIN
            PRINT '  REUSE: Employee row id=' + CAST(@EmployeeId AS NVARCHAR(20)) + ' for ' + @EmpFirstname + ' ' + @EmpLastname + ' (already exists)';
        END

        -- Link User → Employee (idempotent: skip if already set)
        DECLARE @CurrentEmployeeId BIGINT;
        DECLARE @CurrentVendorIdE  BIGINT;
        SELECT @CurrentEmployeeId = [EmployeeId], @CurrentVendorIdE = [VendorId]
        FROM dbo.[User] WHERE [Id] = @UserId;

        IF @CurrentVendorIdE IS NOT NULL
        BEGIN
            PRINT '  WARN: User ' + @UserDisplayName + ' (id=' + CAST(@UserId AS NVARCHAR(20)) + ') already links to Vendor — XOR violation. Manually unlink before re-running.';
        END
        ELSE IF @CurrentEmployeeId = @EmployeeId
        BEGIN
            PRINT '  SKIP: User ' + @UserDisplayName + ' already linked to Employee id=' + CAST(@EmployeeId AS NVARCHAR(20));
        END
        ELSE
        BEGIN
            UPDATE dbo.[User]
            SET    [EmployeeId] = @EmployeeId,
                   [VendorId]   = NULL,
                   [ModifiedDatetime] = SYSUTCDATETIME()
            WHERE  [Id] = @UserId;
            PRINT '  LINKED: User ' + @UserDisplayName + ' (id=' + CAST(@UserId AS NVARCHAR(20)) + ') → Employee id=' + CAST(@EmployeeId AS NVARCHAR(20));
        END
    END
    ELSE IF @WorkerType = 'vendor'
    BEGIN
        DECLARE @VendorId         BIGINT = NULL;
        DECLARE @CurrentEmployeeIdV BIGINT;
        DECLARE @CurrentVendorId    BIGINT;

        SELECT @VendorId = [Id]
        FROM dbo.[Vendor]
        WHERE [Name] = @VendorName AND [IsDeleted] = 0;

        IF @VendorId IS NULL
        BEGIN
            PRINT '  SKIP: Vendor.Name=' + @VendorName + ' not found.';
            GOTO NextRow;
        END

        SELECT @CurrentEmployeeIdV = [EmployeeId], @CurrentVendorId = [VendorId]
        FROM dbo.[User] WHERE [Id] = @UserId;

        IF @CurrentEmployeeIdV IS NOT NULL
        BEGIN
            PRINT '  WARN: User ' + @UserDisplayName + ' (id=' + CAST(@UserId AS NVARCHAR(20)) + ') already links to Employee — XOR violation. Manually unlink before re-running.';
        END
        ELSE IF @CurrentVendorId = @VendorId
        BEGIN
            PRINT '  SKIP: User ' + @UserDisplayName + ' already linked to Vendor id=' + CAST(@VendorId AS NVARCHAR(20));
        END
        ELSE
        BEGIN
            UPDATE dbo.[User]
            SET    [VendorId]   = @VendorId,
                   [EmployeeId] = NULL,
                   [ModifiedDatetime] = SYSUTCDATETIME()
            WHERE  [Id] = @UserId;
            PRINT '  LINKED: User ' + @UserDisplayName + ' (id=' + CAST(@UserId AS NVARCHAR(20)) + ') → Vendor id=' + CAST(@VendorId AS NVARCHAR(20)) + ' (' + @VendorName + ')';
        END
    END

    NextRow:
    SET @UserId = NULL;
    SET @UserMatchCount = 0;
    FETCH NEXT FROM worker_cursor INTO
        @NameLikePattern, @WorkerType, @VendorName, @EmpFirstname, @EmpLastname, @EmpHourlyRate, @EmpMarkup;
END

CLOSE worker_cursor;
DEALLOCATE worker_cursor;

PRINT '';
PRINT '── Verification ────────────────────────────────────────────────────';
SELECT
    u.[Id]         AS UserId,
    u.[Firstname] + ' ' + ISNULL(u.[Lastname], '') AS Name,
    u.[EmployeeId],
    e.[Firstname] + ' ' + e.[Lastname]              AS EmployeeName,
    u.[VendorId],
    v.[Name]                                        AS VendorName
FROM dbo.[User] u
LEFT JOIN dbo.[Employee] e ON e.[Id] = u.[EmployeeId]
LEFT JOIN dbo.[Vendor]   v ON v.[Id] = u.[VendorId]
WHERE u.[EmployeeId] IS NOT NULL OR u.[VendorId] IS NOT NULL
ORDER BY u.[Lastname], u.[Firstname];
GO
