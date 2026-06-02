-- ============================================================================
-- time_entry_view_team.sql
--
-- Phase 1 of project-scoped TimeEntry visibility (filed 2026-05-26).
--
-- Adds a `CanViewTeam` flag on `dbo.RoleModule` and threads it through the
-- TimeEntry read sprocs + a new `dbo.UserCanAccessTimeEntry` UDF so that
-- non-admin roles holding the flag see TimeEntry rows on projects in their
-- own `dbo.UserProject` set (in addition to their own rows).
--
-- Mirrors the e2d3afb / gap-1 pattern that already scopes Bills / Expenses /
-- BillCredits / Invoices / ContractLabor by UserProject. The IN-subquery
-- against UserProject is the only shape that materializes once at parameter
-- substitution (per gap-1 perf tournament — don't rewrite as per-row EXISTS).
--
-- Idempotent. Safe to re-run. ALTER TABLE guards on column existence.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Schema: add CanViewTeam to RoleModule with DEFAULT 0
-- ----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1
    FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanViewTeam'
)
BEGIN
    ALTER TABLE dbo.RoleModule
        ADD CanViewTeam BIT NOT NULL CONSTRAINT DF_RoleModule_CanViewTeam DEFAULT (0);
END;
GO


-- ----------------------------------------------------------------------------
-- 2. Seed: grant CanViewTeam=1 to Owner / Project Manager / Controller /
--    Tenant Admin on the Time Tracking module. Idempotent UPDATE — re-runs
--    are no-ops once values are set.
-- ----------------------------------------------------------------------------
DECLARE @TimeTrackingModuleId BIGINT = (
    SELECT TOP 1 Id FROM dbo.Module WHERE Name = 'Time Tracking'
);

IF @TimeTrackingModuleId IS NULL
BEGIN
    RAISERROR('Time Tracking module not found in dbo.Module — abort seed', 16, 1);
END;
ELSE
BEGIN
    UPDATE rm
       SET rm.CanViewTeam = 1
      FROM dbo.RoleModule rm
      JOIN dbo.Role r ON r.Id = rm.RoleId
     WHERE rm.ModuleId = @TimeTrackingModuleId
       AND r.Name IN ('Owner', 'Project Manager', 'Controller', 'Tenant Admin')
       AND rm.CanViewTeam <> 1;
END;
GO


-- ----------------------------------------------------------------------------
-- 3. UDF: dbo.UserCanAccessTimeEntry
--
-- Returns 1 iff the actor is system admin, OR the actor created (owns) the
-- entry, OR (actor holds CanViewTeam AND any of the entry's TimeLog rows
-- has a ProjectId in the actor's UserProject set).
--
-- Used by service-layer post-fetch checks on by-id reads + mutation gating.
-- Single-row UDF calls are cheap; the gap-1 perf concern only applies to
-- per-row list-path use.
-- ----------------------------------------------------------------------------
CREATE OR ALTER FUNCTION dbo.UserCanAccessTimeEntry
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @ActorCanViewTeam BIT,
    @TimeEntryId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[TimeEntry] te
                WHERE te.[Id] = @TimeEntryId
                  AND te.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN @ActorCanViewTeam = 1 AND EXISTS (
                SELECT 1
                FROM dbo.[TimeLog] tl
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = tl.[ProjectId]
                WHERE tl.[TimeEntryId] = @TimeEntryId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO


-- ----------------------------------------------------------------------------
-- 4. TimeEntry read sprocs — add @ActorCanViewTeam parameter + extend the
--    actor-scope OR clause to include the project-overlap branch.
-- ----------------------------------------------------------------------------

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


-- ----------------------------------------------------------------------------
-- 5. TimeLog read sprocs — scope inherits from parent TimeEntry; the
--    project-overlap branch joins through the TimeLog row itself.
-- ----------------------------------------------------------------------------

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


-- ----------------------------------------------------------------------------
-- 6. TimeEntryStatus read sprocs — scope via parent TimeEntry; same
--    project-overlap branch.
-- ----------------------------------------------------------------------------

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
    ORDER BY s.[CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
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
    ORDER BY s.[CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


-- ----------------------------------------------------------------------------
-- 6b. TimeEntry + TimeLog mutation sprocs — extend scope OR clause so a
--     PM holding can_view_team can edit / delete team rows on their projects.
-- ----------------------------------------------------------------------------

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
        [UserId] = @UserId,
        [WorkDate] = @WorkDate,
        [Note] = @Note
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
        tl.[ClockIn] = @ClockIn,
        tl.[ClockOut] = @ClockOut,
        tl.[LogType] = @LogType,
        tl.[Duration] = @Duration,
        tl.[Latitude] = @Latitude,
        tl.[Longitude] = @Longitude,
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


-- ----------------------------------------------------------------------------
-- 7. RoleModule CRUD sprocs — extend to round-trip the new CanViewTeam
--    column so RoleModuleRepository._from_db can read it and admin UI
--    updates persist it. Identical shape to existing Can* fields.
-- ----------------------------------------------------------------------------

CREATE OR ALTER PROCEDURE dbo.ReadRoleModules
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule];
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [Id] = @Id;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [PublicId] = @PublicId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByRoleId
(
    @RoleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [RoleId] = @RoleId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadRoleModuleByModuleId
(
    @ModuleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete],
        [CanViewTeam]
    FROM dbo.[RoleModule]
    WHERE [ModuleId] = @ModuleId;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.CreateRoleModule
(
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0,
    @CanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2 = SYSUTCDATETIME();
    INSERT INTO dbo.[RoleModule] (
        [CreatedDatetime], [ModifiedDatetime], [RoleId], [ModuleId],
        [CanCreate], [CanRead], [CanUpdate], [CanDelete],
        [CanSubmit], [CanApprove], [CanComplete], [CanViewTeam]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RoleId], INSERTED.[ModuleId],
        INSERTED.[CanCreate], INSERTED.[CanRead], INSERTED.[CanUpdate], INSERTED.[CanDelete],
        INSERTED.[CanSubmit], INSERTED.[CanApprove], INSERTED.[CanComplete],
        INSERTED.[CanViewTeam]
    VALUES (
        @Now, @Now, @RoleId, @ModuleId,
        @CanCreate, @CanRead, @CanUpdate, @CanDelete,
        @CanSubmit, @CanApprove, @CanComplete, @CanViewTeam
    );
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.UpdateRoleModuleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @RoleId BIGINT,
    @ModuleId BIGINT,
    @CanCreate BIT = 0,
    @CanRead BIT = 0,
    @CanUpdate BIT = 0,
    @CanDelete BIT = 0,
    @CanSubmit BIT = 0,
    @CanApprove BIT = 0,
    @CanComplete BIT = 0,
    @CanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;
    UPDATE dbo.[RoleModule]
       SET [ModifiedDatetime] = SYSUTCDATETIME(),
           [RoleId]      = @RoleId,
           [ModuleId]    = @ModuleId,
           [CanCreate]   = @CanCreate,
           [CanRead]     = @CanRead,
           [CanUpdate]   = @CanUpdate,
           [CanDelete]   = @CanDelete,
           [CanSubmit]   = @CanSubmit,
           [CanApprove]  = @CanApprove,
           [CanComplete] = @CanComplete,
           [CanViewTeam] = @CanViewTeam
        OUTPUT
            INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
            CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
            INSERTED.[RoleId], INSERTED.[ModuleId],
            INSERTED.[CanCreate], INSERTED.[CanRead], INSERTED.[CanUpdate], INSERTED.[CanDelete],
            INSERTED.[CanSubmit], INSERTED.[CanApprove], INSERTED.[CanComplete],
            INSERTED.[CanViewTeam]
     WHERE [Id] = @Id
       AND [RowVersion] = @RowVersion;
    COMMIT TRANSACTION;
END;
GO


-- ----------------------------------------------------------------------------
-- 8. Post-migration sanity checks (run AFTER everything above completes).
--    These are SELECTs only — copy/paste and confirm the values.
-- ----------------------------------------------------------------------------
PRINT '--- post-migration sanity ---';

SELECT 'RoleModule.CanViewTeam column exists' AS Check_Item,
       CASE WHEN EXISTS (
            SELECT 1 FROM sys.columns
            WHERE object_id = OBJECT_ID('dbo.RoleModule') AND name = 'CanViewTeam'
        ) THEN 'OK' ELSE 'MISSING' END AS Result;

SELECT r.Id AS RoleId, r.Name AS RoleName, rm.CanViewTeam
  FROM dbo.RoleModule rm
  JOIN dbo.Role r ON r.Id = rm.RoleId
 WHERE rm.ModuleId = (SELECT Id FROM dbo.Module WHERE Name = 'Time Tracking')
   AND rm.CanViewTeam = 1
 ORDER BY r.Name;

SELECT 'dbo.UserCanAccessTimeEntry UDF exists' AS Check_Item,
       CASE WHEN OBJECT_ID('dbo.UserCanAccessTimeEntry') IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS Result;

-- expect 16 TimeEntry/TimeLog/TimeEntryStatus sprocs + 6 RoleModule sprocs = 22 rows
SELECT name AS Sproc, 'OK' AS Result
  FROM sys.procedures
 WHERE name IN (
    'ReadTimeEntries','ReadTimeEntryById','ReadTimeEntryByPublicId',
    'ReadTimeEntriesByUserId','ReadTimeEntriesByProjectId',
    'ReadTimeEntriesPaginated','CountTimeEntries',
    'UpdateTimeEntryById','DeleteTimeEntryById',
    'ReadTimeLogsByTimeEntryId','ReadTimeLogById','ReadTimeLogByPublicId',
    'UpdateTimeLogById','DeleteTimeLogById',
    'ReadTimeEntryStatusesByTimeEntryId','ReadCurrentTimeEntryStatus',
    'ReadRoleModules','ReadRoleModuleById','ReadRoleModuleByPublicId',
    'ReadRoleModuleByRoleId','ReadRoleModuleByModuleId',
    'CreateRoleModule','UpdateRoleModuleById'
 )
 ORDER BY name;
