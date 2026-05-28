-- =============================================================================
-- 2026-05-28 — Phase 2 backfill: set Vendor.HourlyRate + Vendor.Markup from
-- the legacy VENDOR_CONFIG dict, and bind User.VendorId for the 6 workers
-- with matching User rows.
--
-- Per decision #11 (per-hour rate math) the stored 240/260/etc. values were
-- daily 8h-equivalents, so we divide by 8 to get hourly.
--
-- Mapping table — derived 2026-05-28 by name match:
--
--   VENDOR_CONFIG name                | Vendor.Id | User.Id | hourly  | markup
--   ----------------------------------+-----------+---------+---------+-------
--   Denis Samuel Marcia Izaguirre     | 256       | (none)  | 30.0000 | 0.50
--   Wilmer Diaz                       | 1056      | 41      | 32.5000 | 0.50
--   Elmer Cordova                     | 293       | 37      | 32.5000 | 0.50
--   Emilson O. Cordova Tercero        | 300       | 38      | 46.2500 | 0.50
--   Selvin Humberto Cordova Tercero   | 816       | 40      | 62.5000 | 0.35
--   Michael Jacobson                  | 618       | 39      | 30.0000 | 0.50
--   Brayan Rafael Marcia Salina       | 1106      | 36      | 30.0000 | 0.50
--
-- Idempotent: only overwrites NULL HourlyRate / Markup (so a manual edit
-- on the Vendor row beforehand won't be clobbered). Idempotent on
-- User.VendorId too (only updates rows where VendorId IS NULL).
--
-- RUN: .venv/bin/python scripts/run_sql.py scripts/backfill_vendor_rates_and_user_links.sql
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- ── Vendor rate backfill (only fill NULLs, don't overwrite manual edits) ────

UPDATE v
SET v.[HourlyRate] = m.HourlyRate,
    v.[Markup]     = m.Markup,
    v.[ModifiedDatetime] = SYSUTCDATETIME()
FROM dbo.[Vendor] v
INNER JOIN (
    VALUES
        (256,  CAST(30.0000 AS DECIMAL(18,4)), CAST(0.50 AS DECIMAL(18,4))),
        (1056, CAST(32.5000 AS DECIMAL(18,4)), CAST(0.50 AS DECIMAL(18,4))),
        (293,  CAST(32.5000 AS DECIMAL(18,4)), CAST(0.50 AS DECIMAL(18,4))),
        (300,  CAST(46.2500 AS DECIMAL(18,4)), CAST(0.50 AS DECIMAL(18,4))),
        (816,  CAST(62.5000 AS DECIMAL(18,4)), CAST(0.35 AS DECIMAL(18,4))),
        (618,  CAST(30.0000 AS DECIMAL(18,4)), CAST(0.50 AS DECIMAL(18,4))),
        (1106, CAST(30.0000 AS DECIMAL(18,4)), CAST(0.50 AS DECIMAL(18,4)))
) AS m(VendorId, HourlyRate, Markup) ON m.VendorId = v.[Id]
WHERE v.[HourlyRate] IS NULL AND v.[Markup] IS NULL;

PRINT CONCAT('Vendor rate rows updated: ', @@ROWCOUNT);
GO


-- ── User.VendorId backfill (only fill NULLs) ────────────────────────────────

UPDATE u
SET u.[VendorId] = m.VendorId,
    u.[ModifiedDatetime] = SYSUTCDATETIME()
FROM dbo.[User] u
INNER JOIN (
    VALUES
        (41, 1056),  -- Wilmer Diaz
        (37, 293),   -- Elmer Cordova
        (38, 300),   -- Emilson Cordova → Emilson O. Cordova Tercero
        (40, 816),   -- Selvin Cordova → Selvin Humberto Cordova Tercero
        (39, 618),   -- Michael Jacobson
        (36, 1106)   -- Brayan Marcia Salina → Brayan Rafael Marcia Salina
) AS m(UserId, VendorId) ON m.UserId = u.[Id]
WHERE u.[VendorId] IS NULL AND u.[EmployeeId] IS NULL;

PRINT CONCAT('User.VendorId rows updated: ', @@ROWCOUNT);
GO


-- ── Verification ─────────────────────────────────────────────────────────────

PRINT '── Verification ────────────────────────────────────────';

SELECT v.Id AS VendorId, v.Name, v.HourlyRate, v.Markup,
       u.Id AS UserId, u.Firstname + ' ' + u.Lastname AS UserName
FROM dbo.[Vendor] v
LEFT JOIN dbo.[User] u ON u.VendorId = v.Id
WHERE v.Id IN (256, 1056, 293, 300, 816, 618, 1106)
ORDER BY v.Id;
