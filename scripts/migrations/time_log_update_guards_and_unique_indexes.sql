-- =============================================================================
-- time_log_update_guards_and_unique_indexes.sql  (2026-06-10)
--
-- Round-2 review remediation (see build.one.api/TODO.md "Time-tracking
-- round-2 review"):
--
-- 1. NULL-overwrite guards on UpdateTimeLogById / UpdateTimeEntryById.
--    The 2026-05-26 time_entry_view_team.sql migration copied the base
--    sprocs' unconditional SET pattern. Guards are added ONLY for fields
--    that can never be legitimately nulled by an update:
--      - TimeLog.ClockIn / LogType (NOT NULL by schema)
--      - TimeLog.Latitude / Longitude (GPS evidence is append-only by
--        product intent — a partial update must never erase a recorded fix)
--      - TimeEntry.UserId / WorkDate (NOT NULL by schema)
--      - TimeEntry.Note (iOS sends non-null always; clearing uses '')
--    ClockOut / Duration / ProjectId / TimeLog.Note stay unconditional:
--    NULL is a legitimate target value there (reopening a log clears
--    ClockOut; clearing a note sends NULL).
--
-- 2. Unique indexes the iOS duplicate-claim recovery assumes exist:
--      UX_TimeLog_TimeEntry_ClockIn   ON dbo.TimeLog(TimeEntryId, ClockIn)
--      UX_TimeEntry_UserId_WorkDate   ON dbo.TimeEntry(UserId, WorkDate)
--    Each creation is GATED on a duplicate audit: if duplicates already
--    exist in prod (created during the unprotected retry window), the
--    script prints the offending groups and SKIPS the index. Dedup those
--    rows manually, then re-run this script (idempotent).
--
-- Run with: python scripts/run_sql.py scripts/migrations/time_log_update_guards_and_unique_indexes.sql
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-045, 2026-07-16) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   NULL-overwrite guards on UpdateTimeLogById / UpdateTimeEntryById for fields
--   that must never be nulled by a partial update (ClockIn, LogType, GPS, UserId,
--   WorkDate, Note semantics documented in the header above).
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/time_entry/sql/dbo.time_entry.sql
--
-- Sprocs formerly redefined here (now canonical in the base file):
--   dbo.UpdateTimeEntryById, dbo.UpdateTimeLogById
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------

-- =============================================================================
-- Unique index: dbo.TimeLog (TimeEntryId, ClockIn)
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM dbo.[TimeLog]
    GROUP BY [TimeEntryId], [ClockIn]
    HAVING COUNT(*) > 1
)
BEGIN
    PRINT 'WARNING: duplicate (TimeEntryId, ClockIn) groups exist — UX_TimeLog_TimeEntry_ClockIn NOT created. Dedup these rows, then re-run:';
    SELECT [TimeEntryId], CONVERT(VARCHAR(23), [ClockIn], 121) AS [ClockIn], COUNT(*) AS [DupCount]
    FROM dbo.[TimeLog]
    GROUP BY [TimeEntryId], [ClockIn]
    HAVING COUNT(*) > 1;
END
ELSE IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE [name] = 'UX_TimeLog_TimeEntry_ClockIn'
      AND [object_id] = OBJECT_ID('dbo.TimeLog')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UX_TimeLog_TimeEntry_ClockIn]
        ON dbo.[TimeLog]([TimeEntryId], [ClockIn]);
    PRINT 'Created UX_TimeLog_TimeEntry_ClockIn.';
END
ELSE
    PRINT 'UX_TimeLog_TimeEntry_ClockIn already exists — skipped.';
GO

-- =============================================================================
-- Unique index: dbo.TimeEntry (UserId, WorkDate)
-- One entry per user per day is the data model both clients assume
-- (iOS todayEntry / fetchByUserAndDate, the duplicate-claim recovery,
-- and the web week views).
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM dbo.[TimeEntry]
    GROUP BY [UserId], [WorkDate]
    HAVING COUNT(*) > 1
)
BEGIN
    PRINT 'WARNING: duplicate (UserId, WorkDate) groups exist — UX_TimeEntry_UserId_WorkDate NOT created. Dedup these rows, then re-run:';
    SELECT [UserId], CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate], COUNT(*) AS [DupCount]
    FROM dbo.[TimeEntry]
    GROUP BY [UserId], [WorkDate]
    HAVING COUNT(*) > 1;
END
ELSE IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE [name] = 'UX_TimeEntry_UserId_WorkDate'
      AND [object_id] = OBJECT_ID('dbo.TimeEntry')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UX_TimeEntry_UserId_WorkDate]
        ON dbo.[TimeEntry]([UserId], [WorkDate]);
    PRINT 'Created UX_TimeEntry_UserId_WorkDate.';
END
ELSE
    PRINT 'UX_TimeEntry_UserId_WorkDate already exists — skipped.';
GO
