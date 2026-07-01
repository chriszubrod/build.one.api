-- =============================================================================
-- 2026-07-01 — allow @SortBy = 'Worker' in ReadTimeEntries.
--
-- Adds Worker as a sortable column for the /time-entry/list web UI's new
-- column-header sort control. Value maps to (u.Lastname, u.Firstname) so
-- alphabetical grouping matches how the office refers to workers.
--
-- Backward compatible: the existing 'WorkDate' + 'CreatedDatetime' values
-- still route through the same CASE cascade. The tail tie-breakers
-- (u.Lastname, u.Firstname) remain so any non-Worker primary sort still
-- groups by worker within ties.
--
-- Idempotent (CREATE OR ALTER). SET NOCOUNT ON per pyodbc discipline.
-- BEGIN TRAN preserved to match the sibling read sprocs' shape.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


CREATE OR ALTER PROCEDURE ReadTimeEntries
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @UserId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL,
    @SortBy NVARCHAR(50) = 'WorkDate',
    @SortDirection NVARCHAR(4) = 'DESC'
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;

    SELECT
        te.[Id],
        te.[PublicId],
        te.[RowVersion],
        CONVERT(VARCHAR(19), te.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), te.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        te.[UserId],
        CONVERT(VARCHAR(10), te.[WorkDate], 120) AS [WorkDate],
        te.[Note]
    FROM dbo.[TimeEntry] te
    LEFT JOIN dbo.[User] u ON te.[UserId] = u.[Id]
    OUTER APPLY (
        SELECT TOP 1 s.[Status]
        FROM dbo.[TimeEntryStatus] s
        WHERE s.[TimeEntryId] = te.[Id]
        ORDER BY s.[CreatedDatetime] DESC
    ) cs
    WHERE
        (@SearchTerm IS NULL OR
         te.[Note] LIKE '%' + @SearchTerm + '%' OR
         u.[Firstname] LIKE '%' + @SearchTerm + '%' OR
         u.[Lastname] LIKE '%' + @SearchTerm + '%')
        AND (@UserId IS NULL OR te.[UserId] = @UserId)
        AND (@ProjectId IS NULL OR EXISTS (
            SELECT 1 FROM dbo.[TimeLog] tl WHERE tl.[TimeEntryId] = te.[Id] AND tl.[ProjectId] = @ProjectId
        ))
        AND (@Status IS NULL OR cs.[Status] = @Status)
        AND (@StartDate IS NULL OR te.[WorkDate] >= @StartDate)
        AND (@EndDate IS NULL OR te.[WorkDate] <= @EndDate)
    ORDER BY
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'WorkDate' THEN te.[WorkDate] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'WorkDate' THEN te.[WorkDate] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'CreatedDatetime' THEN te.[CreatedDatetime] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'CreatedDatetime' THEN te.[CreatedDatetime] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'Worker' THEN u.[Lastname] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'Worker' THEN u.[Lastname] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'Worker' THEN u.[Firstname] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'Worker' THEN u.[Firstname] END DESC,
        u.[Lastname] ASC,
        u.[Firstname] ASC,
        te.[WorkDate] DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    COMMIT TRANSACTION;
END;
GO


PRINT 'ReadTimeEntries updated with Worker sort support.';
