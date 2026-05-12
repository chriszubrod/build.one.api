-- Phase 3 follow-up (2026-05-12) — remove the `@ActorUserId IS NULL`
-- legacy-caller bypass from TimeEntry / TimeLog / TimeEntryStatus
-- read sprocs. Replaces 001_phase3_scope_by_user.sql.
--
-- Background: 001 included `OR @ActorUserId IS NULL` in every WHERE
-- clause so that pre-Phase-3 callers (service code that hadn't yet
-- learned to thread actor context) would keep working during the
-- staged deploy. Service code is fully rolled out now, so the clause
-- has become a silent leak path — any caller that fails to populate
-- the `current_user_id` ContextVar (e.g., a regressed auth middleware,
-- a scheduler endpoint that forgot to set system context) silently
-- returns every row instead of failing closed.
--
-- This migration removes the bypass everywhere. New scope rule:
--   System admins (@ActorIsSystemAdmin = 1) bypass.
--   Otherwise the row's parent TimeEntry.UserId must match
--     @ActorUserId. NULL @ActorUserId → no rows (fail closed).
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
--
-- Affected paths: every read on TimeEntry and its children. Create
-- paths unaffected. Scheduler / system callers that legitimately need
-- to see all rows must set `current_is_system_admin = True` in the
-- ContextVar before invoking the service — see commit notes.

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO

-- ============================================
-- TimeEntry reads
-- ============================================

CREATE OR ALTER PROCEDURE ReadTimeEntries
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
    FROM dbo.[TimeEntry]
    WHERE
        @ActorIsSystemAdmin = 1
        OR [UserId] = @ActorUserId
    ORDER BY [WorkDate] DESC, [UserId] ASC;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadTimeEntryById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
    FROM dbo.[TimeEntry]
    WHERE [Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR [UserId] = @ActorUserId
      );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadTimeEntryByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
    FROM dbo.[TimeEntry]
    WHERE [PublicId] = @PublicId
      AND (
            @ActorIsSystemAdmin = 1
            OR [UserId] = @ActorUserId
      );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadTimeEntriesByUserId
(
    @UserId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
    FROM dbo.[TimeEntry]
    WHERE [UserId] = @UserId
      AND (
            @ActorIsSystemAdmin = 1
            OR [UserId] = @ActorUserId
      )
    ORDER BY [WorkDate] DESC;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadTimeEntriesByProjectId
(
    @ProjectId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
    FROM dbo.[TimeEntry]
    WHERE EXISTS (
        SELECT 1 FROM dbo.[TimeLog] tl WHERE tl.[TimeEntryId] = [TimeEntry].[Id] AND tl.[ProjectId] = @ProjectId
    )
      AND (
            @ActorIsSystemAdmin = 1
            OR [UserId] = @ActorUserId
      )
    ORDER BY [WorkDate] DESC, [UserId] ASC;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadTimeEntriesPaginated
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
    @ActorIsSystemAdmin BIT = NULL
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
        AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
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

CREATE OR ALTER PROCEDURE CountTimeEntries
(
    @SearchTerm NVARCHAR(255) = NULL,
    @UserId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT COUNT(*) AS [TotalCount]
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
        AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
        );

    COMMIT TRANSACTION;
END;
GO

-- ============================================
-- TimeEntry mutations
-- ============================================

CREATE OR ALTER PROCEDURE UpdateTimeEntryById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @WorkDate DATE,
    @Note NVARCHAR(MAX) NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[TimeEntry]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [WorkDate] = @WorkDate,
        [Note] = @Note
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
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
      );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteTimeEntryById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[TimeEntry]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        CONVERT(VARCHAR(10), DELETED.[WorkDate], 120) AS [WorkDate],
        DELETED.[Note]
    WHERE [Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR [UserId] = @ActorUserId
      );

    COMMIT TRANSACTION;
END;
GO

-- ============================================
-- TimeLog reads (scope via TimeEntry.UserId)
-- ============================================

CREATE OR ALTER PROCEDURE ReadTimeLogsByTimeEntryId
(
    @TimeEntryId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
      )
    ORDER BY tl.[ClockIn] ASC;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadTimeLogById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
      );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadTimeLogByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
      );

    COMMIT TRANSACTION;
END;
GO

-- ============================================
-- TimeLog mutations (scope via TimeEntry.UserId)
-- ============================================

CREATE OR ALTER PROCEDURE UpdateTimeLogById
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
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE tl
    SET
        tl.[ModifiedDatetime] = @Now,
        tl.[ClockIn] = @ClockIn,
        tl.[ClockOut] = @ClockOut,
        tl.[LogType] = @LogType,
        tl.[Duration] = @Duration,
        tl.[Latitude] = @Latitude,
        tl.[Longitude] = @Longitude,
        tl.[ProjectId] = @ProjectId,
        tl.[Note] = @Note
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TimeEntryId],
        CONVERT(VARCHAR(23), INSERTED.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), INSERTED.[ClockOut], 121) AS [ClockOut],
        INSERTED.[LogType],
        INSERTED.[Duration],
        INSERTED.[Latitude],
        INSERTED.[Longitude],
        INSERTED.[ProjectId],
        INSERTED.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = tl.[TimeEntryId]
    WHERE tl.[Id] = @Id
      AND tl.[RowVersion] = @RowVersion
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
      );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteTimeLogById
(
    @Id BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE tl
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[TimeEntryId],
        CONVERT(VARCHAR(23), DELETED.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), DELETED.[ClockOut], 121) AS [ClockOut],
        DELETED.[LogType],
        DELETED.[Duration],
        DELETED.[Latitude],
        DELETED.[Longitude],
        DELETED.[ProjectId],
        DELETED.[Note]
    FROM dbo.[TimeLog] tl
    INNER JOIN dbo.[TimeEntry] te ON te.[Id] = tl.[TimeEntryId]
    WHERE tl.[Id] = @Id
      AND (
            @ActorIsSystemAdmin = 1
            OR te.[UserId] = @ActorUserId
      );

    COMMIT TRANSACTION;
END;
GO

-- ============================================
-- TimeEntryStatus reads (scope via TimeEntry.UserId)
-- ============================================

CREATE OR ALTER PROCEDURE ReadTimeEntryStatusesByTimeEntryId
(
    @TimeEntryId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
      )
    ORDER BY s.[CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentTimeEntryStatus
(
    @TimeEntryId BIGINT,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
      )
    ORDER BY s.[CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

PRINT 'Phase 3 TimeEntry/TimeLog/TimeEntryStatus scope filter applied.';
