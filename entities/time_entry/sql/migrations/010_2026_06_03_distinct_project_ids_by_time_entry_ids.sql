-- =============================================================================
-- 2026-06-03 — Batch lookup: distinct ProjectIds per TimeEntry.
--
-- Powers the React TimeEntry list page's new Project column. Replaces an
-- N+1 read where the list endpoint would otherwise fetch TimeLogs per
-- entry. Input is a comma-separated list of TimeEntryIds (matches the
-- STRING_SPLIT pattern used elsewhere in the codebase — see
-- ReadExpenseLineItemAttachmentsByExpenseLineItemPublicIds).
-- =============================================================================

CREATE OR ALTER PROCEDURE dbo.ReadDistinctProjectIdsByTimeEntryIds
(
    @TimeEntryIds NVARCHAR(MAX)  -- CSV of BIGINT TimeEntry.Ids
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @TimeEntryIds IS NULL OR LEN(@TimeEntryIds) = 0
    BEGIN
        SELECT TOP 0 CAST(0 AS BIGINT) AS TimeEntryId, CAST(0 AS BIGINT) AS ProjectId;
        RETURN;
    END

    -- DISTINCT (TimeEntryId, ProjectId). NULL ProjectId on TimeLog is
    -- legitimate (break logs / un-assigned work) — surface as NULL so
    -- the caller can show an "(unassigned)" marker if it wants. Work
    -- and break LogTypes both included; consumer can filter.
    SELECT DISTINCT
        tl.[TimeEntryId],
        tl.[ProjectId]
    FROM dbo.[TimeLog] tl
    INNER JOIN (
        SELECT CAST(LTRIM(RTRIM(value)) AS BIGINT) AS Id
        FROM STRING_SPLIT(@TimeEntryIds, ',')
        WHERE LTRIM(RTRIM(value)) <> ''
    ) ids ON ids.Id = tl.[TimeEntryId]
    ORDER BY tl.[TimeEntryId], tl.[ProjectId];
END;
GO


PRINT 'ReadDistinctProjectIdsByTimeEntryIds created.';
