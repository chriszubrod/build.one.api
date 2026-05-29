-- =============================================================================
-- 2026-05-27 — Phase 2 backfill: VENDOR_CONFIG rate/markup → Vendor table.
--
-- Populates dbo.Vendor.HourlyRate + Vendor.Markup for the 6 active 1099
-- contractors using the exact values from VENDOR_CONFIG today. After this
-- runs, the import_service + bill_service rate lookups will resolve via
-- Vendor.HourlyRate first; the VENDOR_CONFIG dict fallback only fires if
-- a vendor's rate column ends up NULL (transition safety).
--
-- Selvin Humberto Cordova Tercero is DELIBERATELY OMITTED — he's now an
-- Employee (Phase 1g backfill), and his rate lives on dbo.Employee.HourlyRate.
-- Leaving his old Vendor row's HourlyRate as NULL prevents accidental
-- double-billing if a stale TimeEntry sneaks in via the Excel path.
--
-- Idempotent — uses CASE WHEN preserve-on-non-NULL so re-runs don't clobber
-- rates that an admin may have edited via the React Vendor edit page.
--
-- Run via:
--   python scripts/run_sql.py scripts/backfill_vendor_rates.sql
--
-- Prereq: entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


DECLARE @Rates TABLE (
    VendorName  NVARCHAR(450) NOT NULL,
    HourlyRate  DECIMAL(18,4) NOT NULL,
    Markup      DECIMAL(18,4) NOT NULL
);

-- Values lifted verbatim from VENDOR_CONFIG in
-- entities/contract_labor/business/bill_service.py (as of 2026-05-27).
INSERT INTO @Rates VALUES
    (N'Denis Samuel Marcia Izaguirre', 240.0000, 0.5000),
    (N'Wilmer Diaz',                   260.0000, 0.5000),
    (N'Elmer Cordova',                 260.0000, 0.5000),
    (N'Emilson O. Cordova Tercero',    370.0000, 0.5000),
    (N'Michael Jacobson',              240.0000, 0.5000),
    (N'Brayan Rafael Marcia Salina',   240.0000, 0.5000);

DECLARE @VendorName  NVARCHAR(450);
DECLARE @HourlyRate  DECIMAL(18,4);
DECLARE @Markup      DECIMAL(18,4);
DECLARE @VendorId    BIGINT;
DECLARE @CurrentRate DECIMAL(18,4);

DECLARE rates_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT VendorName, HourlyRate, Markup FROM @Rates;

OPEN rates_cursor;
FETCH NEXT FROM rates_cursor INTO @VendorName, @HourlyRate, @Markup;

WHILE @@FETCH_STATUS = 0
BEGIN
    SELECT @VendorId = [Id], @CurrentRate = [HourlyRate]
    FROM dbo.[Vendor]
    WHERE [Name] = @VendorName AND [IsDeleted] = 0;

    IF @VendorId IS NULL
    BEGIN
        PRINT '  SKIP: Vendor.Name=' + @VendorName + ' not found.';
    END
    ELSE IF @CurrentRate IS NOT NULL
    BEGIN
        PRINT '  SKIP: Vendor.Name=' + @VendorName + ' already has HourlyRate='
              + CAST(@CurrentRate AS NVARCHAR(20)) + ' — preserving (re-run safety).';
    END
    ELSE
    BEGIN
        UPDATE dbo.[Vendor]
        SET    [HourlyRate]       = @HourlyRate,
               [Markup]           = @Markup,
               [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE  [Id] = @VendorId;
        PRINT '  SET: Vendor.Name=' + @VendorName + ' → rate=' + CAST(@HourlyRate AS NVARCHAR(20))
              + ', markup=' + CAST(@Markup AS NVARCHAR(20));
    END

    SET @VendorId = NULL;
    SET @CurrentRate = NULL;
    FETCH NEXT FROM rates_cursor INTO @VendorName, @HourlyRate, @Markup;
END

CLOSE rates_cursor;
DEALLOCATE rates_cursor;

PRINT '';
PRINT '── Verification ────────────────────────────────────────────────────';
SELECT
    [Id]   AS VendorId,
    [Name] AS VendorName,
    [HourlyRate],
    [Markup],
    [IsContractLabor]
FROM dbo.[Vendor]
WHERE [Name] IN (
    N'Denis Samuel Marcia Izaguirre',
    N'Wilmer Diaz',
    N'Elmer Cordova',
    N'Emilson O. Cordova Tercero',
    N'Selvin Humberto Cordova Tercero',
    N'Michael Jacobson',
    N'Brayan Rafael Marcia Salina'
)
ORDER BY [Name] ASC;
GO
