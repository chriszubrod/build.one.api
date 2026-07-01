-- =============================================================================
-- 2026-06-16 — batch read current TimeEntryStatus for a list of TimeEntry IDs.
--
-- Fixes an N+1 in the /api/v1/time-entries list endpoint:
-- _entry_dict_with_current_status calls read_current per entry → one
-- sproc call per row. Page size 50 = 50 status sprocs stacked on top of
-- the list + count queries. Same pattern already applied for time_logs
-- (migration 012) and reviews (ReadCurrentReviewsByBillIds).
--
-- Uses ROW_NUMBER() OVER (PARTITION BY TimeEntryId ORDER BY CreatedDatetime
-- DESC, Id DESC) to pick the latest row per entry in a single query.
-- Returns flat rows; the API groups by TimeEntryId client-side.
--
-- Idempotent (CREATE OR ALTER). SET NOCOUNT ON per pyodbc discipline —
-- pure read, no mutations, so no ROLLBACK concern.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


CREATE OR ALTER PROCEDURE ReadCurrentTimeEntryStatusesByTimeEntryIds
(
    @TimeEntryIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH ranked AS (
        SELECT
            s.[Id],
            s.[PublicId],
            s.[RowVersion],
            CONVERT(VARCHAR(19), s.[CreatedDatetime], 120) AS [CreatedDatetime],
            s.[TimeEntryId],
            s.[Status],
            s.[UserId],
            s.[Note],
            ROW_NUMBER() OVER (
                PARTITION BY s.[TimeEntryId]
                ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC
            ) AS rn
        FROM dbo.[TimeEntryStatus] s
        INNER JOIN STRING_SPLIT(ISNULL(@TimeEntryIds, ''), ',') p
            ON p.value <> '' AND s.[TimeEntryId] = TRY_CAST(LTRIM(RTRIM(p.value)) AS BIGINT)
    )
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        [CreatedDatetime],
        [TimeEntryId],
        [Status],
        [UserId],
        [Note]
    FROM ranked
    WHERE rn = 1;
END;
GO


PRINT 'ReadCurrentTimeEntryStatusesByTimeEntryIds created.';
