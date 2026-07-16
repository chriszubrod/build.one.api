-- =====================================================================
-- SUPERSEDED (U-039, 2026-07-16) — DO NOT RE-RUN IN ISOLATION.
-- Canonical source of these sprocs is now:
--   entities/time_entry/sql/dbo.time_entry.sql  (+ scripts/migrations/time_entry_view_team.sql)
-- The 16 sproc bodies below have been reconciled to the CURRENT scoped
-- 3-param RBAC state (@ActorUserId / @ActorIsSystemAdmin / @ActorCanViewTeam,
-- 3-way fail-closed) so a naive re-run is an idempotent no-op, NOT a
-- revert to the old 2-param signature. Kept for historical lineage only.
-- =====================================================================

-- Phase 3 — Access Control Rebuild — TimeEntry / TimeLog / TimeEntryStatus
-- row scoping by UserId.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
--
-- Scope rule:
--   System admins (@ActorIsSystemAdmin = 1) bypass.
--   Legacy callers (@ActorUserId IS NULL) bypass — preserves
--     pre-Phase 3 behavior during the staged deploy. Service code
--     deploy is what activates filtering.
--   Otherwise the row's parent TimeEntry.UserId must match
--     @ActorUserId. For TimeLog and TimeEntryStatus this is enforced
--     by joining through TimeEntry.
--
-- Applies to every read/update/delete on TimeEntry and its children.
-- Create paths are unaffected — callers stamp UserId on insert and
-- the service layer prevents impersonation at the API surface.

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntries
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry] te
    WHERE
        @ActorIsSystemAdmin = 1
        OR te.[UserId] = @ActorUserId
        OR (
            @ActorCanViewTeam = 1
            AND EXISTS (
                SELECT 1
                FROM dbo.[TimeLog] tl
                WHERE tl.[TimeEntryId] = te.[Id]
                  AND tl.[ProjectId] IN (
                    SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                  )
            )
        )
    ORDER BY te.[WorkDate] DESC, te.[UserId] ASC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntryById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry] te
    WHERE te.[Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl
                    WHERE tl.[TimeEntryId] = te.[Id]
                      AND tl.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntryByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry] te
    WHERE te.[PublicId] = @PublicId
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl
                    WHERE tl.[TimeEntryId] = te.[Id]
                      AND tl.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntriesByUserId
(
    @UserId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry] te
    WHERE te.[UserId] = @UserId
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl
                    WHERE tl.[TimeEntryId] = te.[Id]
                      AND tl.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      )
    ORDER BY te.[WorkDate] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntriesByProjectId
(
    @ProjectId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

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
    WHERE EXISTS (
            SELECT 1 FROM dbo.[TimeLog] tl WHERE tl.[TimeEntryId] = te.[Id] AND tl.[ProjectId] = @ProjectId
        )
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[UserProject] up
                    WHERE up.[UserId] = @ActorUserId
                      AND up.[ProjectId] = @ProjectId
                )
            )
      )
    ORDER BY te.[WorkDate] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

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
        u.[Lastname] ASC,
        u.[Firstname] ASC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.CountTimeEntries
(
    @SearchTerm NVARCHAR(255) = NULL,
    @UserId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    -- alias MUST be [TotalCount] — TimeEntryRepository.count() reads row.TotalCount
    SELECT COUNT(*) AS [TotalCount]
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
        );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.UpdateTimeEntryById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @WorkDate DATE,
    @Note NVARCHAR(MAX) NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[TimeEntry]
    SET
        [ModifiedDatetime] = @Now,
        -- NULL guards: NULL means "caller did not supply" — preserve
        -- existing. Clearing an entry note is expressed as '' (the iOS
        -- model is non-optional), never NULL.
        [UserId] = COALESCE(@UserId, [UserId]),
        [WorkDate] = COALESCE(@WorkDate, [WorkDate]),
        [Note] = CASE WHEN @Note IS NULL THEN [Note] ELSE @Note END
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[Note]
    WHERE [Id] = @Id
      AND [RowVersion] = @RowVersion
      AND (
            @ActorIsSystemAdmin = 1
            OR [UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl
                    WHERE tl.[TimeEntryId] = @Id
                      AND tl.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.DeleteTimeEntryById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[TimeEntry]
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        CONVERT(VARCHAR(10), DELETED.[WorkDate], 120) AS [WorkDate],
        DELETED.[Note]
    WHERE [Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR [UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl
                    WHERE tl.[TimeEntryId] = @Id
                      AND tl.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeLogsByTimeEntryId
(
    @TimeEntryId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        tl.[Id],
        tl.[PublicId],
        tl.[RowVersion],
        CONVERT(VARCHAR(19), tl.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), tl.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        tl.[TimeEntryId],
        CONVERT(VARCHAR(23), tl.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), tl.[ClockOut], 121) AS [ClockOut],
        tl.[LogType],
        tl.[Duration],
        tl.[Latitude],
        tl.[Longitude],
        tl.[ProjectId],
        tl.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = tl.[TimeEntryId]
    WHERE tl.[TimeEntryId] = @TimeEntryId
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl_scope
                    WHERE tl_scope.[TimeEntryId] = te.[Id]
                      AND tl_scope.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeLogById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        tl.[Id],
        tl.[PublicId],
        tl.[RowVersion],
        CONVERT(VARCHAR(19), tl.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), tl.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        tl.[TimeEntryId],
        CONVERT(VARCHAR(23), tl.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), tl.[ClockOut], 121) AS [ClockOut],
        tl.[LogType],
        tl.[Duration],
        tl.[Latitude],
        tl.[Longitude],
        tl.[ProjectId],
        tl.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = tl.[TimeEntryId]
    WHERE tl.[Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl_scope
                    WHERE tl_scope.[TimeEntryId] = te.[Id]
                      AND tl_scope.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeLogByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        tl.[Id],
        tl.[PublicId],
        tl.[RowVersion],
        CONVERT(VARCHAR(19), tl.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), tl.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        tl.[TimeEntryId],
        CONVERT(VARCHAR(23), tl.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), tl.[ClockOut], 121) AS [ClockOut],
        tl.[LogType],
        tl.[Duration],
        tl.[Latitude],
        tl.[Longitude],
        tl.[ProjectId],
        tl.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = tl.[TimeEntryId]
    WHERE tl.[PublicId] = @PublicId
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl_scope
                    WHERE tl_scope.[TimeEntryId] = te.[Id]
                      AND tl_scope.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.UpdateTimeLogById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ClockIn DATETIME2(3),
    @ClockOut DATETIME2(3) NULL,
    @LogType NVARCHAR(10),
    @Duration DECIMAL(6,2) NULL,
    @Latitude DECIMAL(9,6) NULL,
    @Longitude DECIMAL(9,6) NULL,
    @ProjectId BIGINT NULL,
    @Note NVARCHAR(MAX) NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE tl
    SET
        tl.[ModifiedDatetime] = @Now,
        -- NULL guards: NOT NULL columns + append-only GPS evidence.
        -- NULL here means "caller did not supply" — preserve existing.
        tl.[ClockIn] = COALESCE(@ClockIn, tl.[ClockIn]),
        tl.[LogType] = COALESCE(@LogType, tl.[LogType]),
        tl.[Latitude] = CASE WHEN @Latitude IS NULL THEN tl.[Latitude] ELSE @Latitude END,
        tl.[Longitude] = CASE WHEN @Longitude IS NULL THEN tl.[Longitude] ELSE @Longitude END,
        -- Unconditional: NULL is a legitimate target value for these.
        tl.[ClockOut] = @ClockOut,
        tl.[Duration] = @Duration,
        tl.[ProjectId] = @ProjectId,
        tl.[Note] = @Note
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TimeEntryId],
        CONVERT(VARCHAR(23), INSERTED.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), INSERTED.[ClockOut], 121) AS [ClockOut],
        INSERTED.[LogType], INSERTED.[Duration],
        INSERTED.[Latitude], INSERTED.[Longitude],
        INSERTED.[ProjectId], INSERTED.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = tl.[TimeEntryId]
    WHERE tl.[Id] = @Id
      AND tl.[RowVersion] = @RowVersion
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl_scope
                    WHERE tl_scope.[TimeEntryId] = te.[Id]
                      AND tl_scope.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.DeleteTimeLogById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE tl
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[TimeEntryId],
        CONVERT(VARCHAR(23), DELETED.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), DELETED.[ClockOut], 121) AS [ClockOut],
        DELETED.[LogType], DELETED.[Duration],
        DELETED.[Latitude], DELETED.[Longitude],
        DELETED.[ProjectId], DELETED.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = tl.[TimeEntryId]
    WHERE tl.[Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl_scope
                    WHERE tl_scope.[TimeEntryId] = te.[Id]
                      AND tl_scope.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      );

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntryStatusesByTimeEntryId
(
    @TimeEntryId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        s.[Id],
        s.[PublicId],
        s.[RowVersion],
        CONVERT(VARCHAR(19), s.[CreatedDatetime], 120) AS [CreatedDatetime],
        s.[TimeEntryId],
        s.[Status],
        s.[UserId],
        s.[Note]
    FROM dbo.[TimeEntryStatus] s
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = s.[TimeEntryId]
    WHERE s.[TimeEntryId] = @TimeEntryId
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl_scope
                    WHERE tl_scope.[TimeEntryId] = te.[Id]
                      AND tl_scope.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      )
    ORDER BY s.[CreatedDatetime] ASC, s.[Id] ASC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadCurrentTimeEntryStatus
(
    @TimeEntryId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        s.[Id],
        s.[PublicId],
        s.[RowVersion],
        CONVERT(VARCHAR(19), s.[CreatedDatetime], 120) AS [CreatedDatetime],
        s.[TimeEntryId],
        s.[Status],
        s.[UserId],
        s.[Note]
    FROM dbo.[TimeEntryStatus] s
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = s.[TimeEntryId]
    WHERE s.[TimeEntryId] = @TimeEntryId
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
            OR (
                @ActorCanViewTeam = 1
                AND EXISTS (
                    SELECT 1 FROM dbo.[TimeLog] tl_scope
                    WHERE tl_scope.[TimeEntryId] = te.[Id]
                      AND tl_scope.[ProjectId] IN (
                        SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                      )
                )
            )
      )
    ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC;

    COMMIT TRANSACTION;
END;
GO

PRINT 'Phase 3 TimeEntry/TimeLog/TimeEntryStatus scope filter applied.';
