-- =============================================================================
-- 2026-06-16 — batch read TimeLogs for a list of TimeEntry IDs.
--
-- Solves the N+1 round-trip on the PastDayScreen Team view: previously
-- the React page fetched the day's TimeEntry list, then fired one detail
-- GET per entry to hydrate time_logs. With ~8 workers/day the user saw
-- the rows render one at a time as each round-trip landed.
--
-- This sproc lets the /api/v1/time-entries list endpoint (with
-- ?include_logs=true) hydrate all logs in a single SQL call after the
-- parent read — collapsing N+1 HTTP round-trips into 1.
--
-- Returns flat rows; the API groups by TimeEntryId client-side. Ordered
-- by (TimeEntryId, ClockIn) so the API can stream-group cheaply.
--
-- Idempotent (CREATE OR ALTER).
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


CREATE OR ALTER PROCEDURE ReadTimeLogsByTimeEntryIds
(
    @TimeEntryIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        tl.[Id],
        tl.[PublicId],
        tl.[RowVersion],
        CONVERT(VARCHAR(19), tl.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), tl.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        tl.[TimeEntryId],
        CONVERT(VARCHAR(23), tl.[ClockIn],  121) AS [ClockIn],
        CONVERT(VARCHAR(23), tl.[ClockOut], 121) AS [ClockOut],
        tl.[LogType],
        tl.[Duration],
        tl.[Latitude],
        tl.[Longitude],
        tl.[ProjectId],
        tl.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN STRING_SPLIT(ISNULL(@TimeEntryIds, ''), ',') s
        ON s.value <> '' AND tl.[TimeEntryId] = TRY_CAST(LTRIM(RTRIM(s.value)) AS BIGINT)
    ORDER BY tl.[TimeEntryId], tl.[ClockIn] ASC;
END;
GO


PRINT 'ReadTimeLogsByTimeEntryIds created.';
