-- Migration 016 adds the @SortBy='Worker' ORDER-BY branch to ReadTimeEntriesPaginated
-- (u.Lastname then u.Firstname, honoring @SortDirection); ORDER-BY-only, no signature/scope/row-set change;
-- preserves the 13-param RBAC-scoped signature and the 3-way fail-closed scope clause.
-- Also appends te.[Id] ASC as the final ORDER BY key so the sort is total and OFFSET/FETCH pagination is deterministic.

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntriesPaginated
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
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
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
        ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC
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
        AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl2
                    WHERE tl2.[TimeEntryId] = te.[Id]
                      AND tl2.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
        )
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
        te.[Id] ASC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    COMMIT TRANSACTION;
END;
GO
