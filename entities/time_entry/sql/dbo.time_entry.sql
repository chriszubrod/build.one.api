-- ============================================================================
-- SINGLE CANONICAL SOURCE (U-045, 2026-07-16): this file is the ONE home for
-- all 19 TimeEntry / TimeLog / TimeEntryStatus stored procedures. No migration
-- may redefine them — change this file and apply it. Enforced by
-- tests/test_sproc_single_source.py. Build order: README.md (same directory).
--
-- The dbo.UserCanAccessTimeEntry UDF moved to shared/sql/dbo.access_udfs.sql
-- in U-051, which is the canonical home for the whole dbo.UserCanAccess*
-- family.
--
-- PROJECT LINK LIVES ON TimeLog (U-057, 2026-07-16): dbo.TimeLog.ProjectId is the
-- ONE home for "which project was worked" — a worker can move between projects
-- inside a single WorkDate, so the link belongs to the clock-in segment, not the
-- day. dbo.TimeEntry has NO ProjectId; the vestigial column + its index are
-- dropped by the guarded block below. Anything deriving project activity from
-- time tracking (e.g. the UserProject grants in intelligence/persistence/sql/
-- onboard.*.sql) MUST read dbo.TimeLog.
--
-- INCIDENT HISTORY (2026-07-15, Unit U-037): migration 015 once redefined 4
-- read/mutation sprocs FROM a stale unscoped copy of this file and dropped the
-- @ActorUserId / @ActorIsSystemAdmin / @ActorCanViewTeam RBAC actor params ->
-- prod 500 (SQL 8144), cross-user payroll exposure risk. The 16 READ + MUTATION
-- sprocs below are RBAC-SCOPED — they carry those actor params + a fail-closed
-- actor-scope WHERE clause.
--
-- CREATE-path sprocs (CreateTimeEntry / CreateTimeLog / CreateTimeEntryStatus)
-- are intentionally UNSCOPED and must stay so.
-- ============================================================================

-- Module registration for RBAC
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Time Tracking')
BEGIN
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    INSERT INTO dbo.[Module] ([Name], [Route], [CreatedDatetime], [ModifiedDatetime])
    VALUES ('Time Tracking', '/time-entries', @Now, @Now);
END
GO


-- TimeEntry Table
-- Stores time tracking entries for workers (vendors and employees) on projects

GO

IF OBJECT_ID('dbo.TimeEntry', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[TimeEntry]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Worker and assignment
    -- NB: the project lives on TimeLog, NOT here — a worker can move between
    -- projects within a single WorkDate, so each clock-in segment carries its
    -- own ProjectId. See dbo.TimeLog below.
    [UserId] BIGINT NOT NULL,                      -- FK to User (the worker)
    [WorkDate] DATE NOT NULL,
    [Note] NVARCHAR(MAX) NULL,                     -- Worker's note, important for reviewer

    CONSTRAINT [FK_TimeEntry_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id])
);
END
GO


-- TimeLog Table
-- Stores raw clock in/out timestamps for time entries (many per TimeEntry)
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[TimeLog]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Parent reference
    [TimeEntryId] BIGINT NOT NULL,

    -- Timestamp data
    [ClockIn] DATETIME2(3) NOT NULL,
    [ClockOut] DATETIME2(3) NULL,                  -- NULL = still clocked in
    [LogType] NVARCHAR(10) NOT NULL DEFAULT 'work', -- 'work' or 'break'
    [Duration] DECIMAL(6,2) NULL,                   -- Calculated from timestamps
    [Latitude] DECIMAL(9,6) NULL,                   -- GPS latitude at clock in/out
    [Longitude] DECIMAL(9,6) NULL,                  -- GPS longitude at clock in/out

    -- The project worked during this segment. NULL is legitimate: break logs
    -- and not-yet-assigned work. This is the ONLY home for the project link —
    -- TimeEntry deliberately has no ProjectId.
    [ProjectId] BIGINT NULL,

    CONSTRAINT [FK_TimeLog_TimeEntry] FOREIGN KEY ([TimeEntryId]) REFERENCES [dbo].[TimeEntry]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_TimeLog_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO

-- Add Latitude/Longitude columns (idempotent migration)
IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.TimeLog') AND name = 'Latitude')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD [Latitude] DECIMAL(9,6) NULL;
END
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.TimeLog') AND name = 'Longitude')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD [Longitude] DECIMAL(9,6) NULL;
END
GO

-- Add ProjectId to TimeLog
IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.TimeLog') AND name = 'ProjectId')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD [ProjectId] BIGINT NULL;
    ALTER TABLE [dbo].[TimeLog] ADD CONSTRAINT [FK_TimeLog_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

-- Add Note to TimeLog
IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.TimeLog') AND name = 'Note')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD [Note] NVARCHAR(MAX) NULL;
END
GO


-- Retire the vestigial TimeEntry.ProjectId (U-057, 2026-07-16).
--
-- The project link lives on dbo.TimeLog.ProjectId, per clock-in segment, because
-- a worker can move between projects inside one WorkDate. An earlier migration
-- moved it there but left this column behind — nullable, unread, and NULL on
-- every row. All 19 sprocs below read tl.[ProjectId]; nothing reads te.[ProjectId].
--
-- GUARDED: aborts if any non-NULL value survives, so an environment that never
-- completed the TimeLog backfill fails loudly instead of silently discarding the
-- project link. Dynamic SQL is required — the column is absent on a fresh build,
-- and a static reference to it would fail to compile this batch.
IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL
   AND EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.TimeEntry') AND name = 'ProjectId')
BEGIN
    DECLARE @UnmigratedRows INT;
    EXEC sp_executesql
        N'SELECT @cnt = COUNT(*) FROM dbo.[TimeEntry] WHERE [ProjectId] IS NOT NULL',
        N'@cnt INT OUTPUT', @cnt = @UnmigratedRows OUTPUT;

    IF @UnmigratedRows > 0
        RAISERROR(
            'dbo.time_entry.sql: %d TimeEntry row(s) still carry a non-NULL ProjectId. Backfill dbo.TimeLog.ProjectId from them before this column can be dropped.',
            16, 1, @UnmigratedRows);
    ELSE
    BEGIN
        IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_TimeEntry_Project')
            ALTER TABLE [dbo].[TimeEntry] DROP CONSTRAINT [FK_TimeEntry_Project];
        IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_ProjectId' AND object_id = OBJECT_ID('dbo.TimeEntry'))
            DROP INDEX [IX_TimeEntry_ProjectId] ON [dbo].[TimeEntry];
        EXEC sp_executesql N'ALTER TABLE [dbo].[TimeEntry] DROP COLUMN [ProjectId]';
    END
END
GO



-- TimeEntryStatus Table
-- Stores full history of status transitions with audit trail
GO

IF OBJECT_ID('dbo.TimeEntryStatus', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[TimeEntryStatus]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,

    -- Status transition
    [TimeEntryId] BIGINT NOT NULL,
    [Status] NVARCHAR(20) NOT NULL,                -- draft, submitted, approved, rejected, billed
    [UserId] BIGINT NOT NULL,                      -- FK to User (who made the change)
    [Note] NVARCHAR(MAX) NULL,                     -- Rejection reason, approval notes, etc.

    CONSTRAINT [FK_TimeEntryStatus_TimeEntry] FOREIGN KEY ([TimeEntryId]) REFERENCES [dbo].[TimeEntry]([Id]),
    CONSTRAINT [FK_TimeEntryStatus_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id])
);
END
GO


-- Indexes
IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_PublicId' AND object_id = OBJECT_ID('dbo.TimeEntry'))
BEGIN
CREATE INDEX IX_TimeEntry_PublicId ON [dbo].[TimeEntry] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_UserId' AND object_id = OBJECT_ID('dbo.TimeEntry'))
BEGIN
CREATE INDEX IX_TimeEntry_UserId ON [dbo].[TimeEntry] ([UserId]);
END
GO

-- (No IX_TimeEntry_ProjectId — the column is retired; see the drop block above.
--  The project-keyed index lives on TimeLog: IX_TimeLog_ProjectId.)

IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_WorkDate' AND object_id = OBJECT_ID('dbo.TimeEntry'))
BEGIN
CREATE INDEX IX_TimeEntry_WorkDate ON [dbo].[TimeEntry] ([WorkDate]);
END
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeLog_TimeEntryId' AND object_id = OBJECT_ID('dbo.TimeLog'))
BEGIN
CREATE INDEX IX_TimeLog_TimeEntryId ON [dbo].[TimeLog] ([TimeEntryId]);
END
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeLog_PublicId' AND object_id = OBJECT_ID('dbo.TimeLog'))
BEGIN
CREATE INDEX IX_TimeLog_PublicId ON [dbo].[TimeLog] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeLog_ProjectId' AND object_id = OBJECT_ID('dbo.TimeLog'))
BEGIN
CREATE INDEX IX_TimeLog_ProjectId ON [dbo].[TimeLog] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.TimeEntryStatus', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntryStatus_TimeEntryId' AND object_id = OBJECT_ID('dbo.TimeEntryStatus'))
BEGIN
CREATE INDEX IX_TimeEntryStatus_TimeEntryId ON [dbo].[TimeEntryStatus] ([TimeEntryId]);
END
GO

IF OBJECT_ID('dbo.TimeEntryStatus', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntryStatus_PublicId' AND object_id = OBJECT_ID('dbo.TimeEntryStatus'))
BEGIN
CREATE INDEX IX_TimeEntryStatus_PublicId ON [dbo].[TimeEntryStatus] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.TimeEntryStatus', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntryStatus_TimeEntryId_CreatedDatetime_Id' AND object_id = OBJECT_ID('dbo.TimeEntryStatus'))
BEGIN
-- Covering index for the 'latest TimeEntryStatus per TimeEntry' resolution used by
-- ReadTimeEntriesPaginated / CountTimeEntries (OUTER APPLY TOP 1) and the batch
-- ReadCurrentTimeEntryStatusesByTimeEntryIds (ROW_NUMBER). Key order (TimeEntryId,
-- CreatedDatetime, Id) serves ORDER BY CreatedDatetime DESC, Id DESC via a backward
-- ordered scan (no Sort); INCLUDE (Status) makes the two APPLY sites lookup-free.
CREATE INDEX IX_TimeEntryStatus_TimeEntryId_CreatedDatetime_Id ON [dbo].[TimeEntryStatus] ([TimeEntryId], [CreatedDatetime], [Id]) INCLUDE ([Status]);
END
GO

-- ============================================
-- TimeEntry Stored Procedures
-- ============================================

GO

CREATE OR ALTER PROCEDURE CreateTimeEntry
(
    @UserId BIGINT,
    @WorkDate DATE,
    @Note NVARCHAR(MAX) NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[TimeEntry] (
        [CreatedDatetime], [ModifiedDatetime], [UserId], [WorkDate], [Note], [CreatedByUserId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[Note]
    VALUES (
        @Now, @Now, @UserId, @WorkDate, @Note, COALESCE(@CreatedByUserId, 17)
    );

    COMMIT TRANSACTION;
END;
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


-- ============================================
-- TimeLog Stored Procedures
-- ============================================

GO

CREATE OR ALTER PROCEDURE CreateTimeLog
(
    @TimeEntryId BIGINT,
    @ClockIn DATETIME2(3),
    @ClockOut DATETIME2(3) NULL,
    @LogType NVARCHAR(10) = 'work',
    @Duration DECIMAL(6,2) NULL,
    @Latitude DECIMAL(9,6) NULL,
    @Longitude DECIMAL(9,6) NULL,
    @ProjectId BIGINT NULL,
    @Note NVARCHAR(MAX) NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[TimeLog] (
        [CreatedDatetime], [ModifiedDatetime], [TimeEntryId], [ClockIn], [ClockOut], [LogType], [Duration], [Latitude], [Longitude], [ProjectId], [Note], [CreatedByUserId]
    )
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
    VALUES (
        @Now, @Now, @TimeEntryId, @ClockIn, @ClockOut, @LogType, @Duration, @Latitude, @Longitude, @ProjectId, @Note, COALESCE(@CreatedByUserId, 17)
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


-- ============================================
-- TimeEntryStatus Stored Procedures
-- ============================================

GO

CREATE OR ALTER PROCEDURE CreateTimeEntryStatus
(
    @TimeEntryId BIGINT,
    @Status NVARCHAR(20),
    @UserId BIGINT,
    @Note NVARCHAR(MAX) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[TimeEntryStatus] (
        [CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        INSERTED.[TimeEntryId],
        INSERTED.[Status],
        INSERTED.[UserId],
        INSERTED.[Note]
    VALUES (
        @Now, @TimeEntryId, @Status, @UserId, @Note
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

-- U-125 (2026-07-23): sprocs below homed from migrations 001-013; bodies are the LIVE prod definitions captured via sys.sql_modules.


CREATE OR ALTER PROCEDURE dbo.AggregateTimeEntryOnSubmit
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @UserId       BIGINT;
    DECLARE @WorkDate     DATE;
    DECLARE @EmployeeId   BIGINT;
    DECLARE @VendorId     BIGINT;
    DECLARE @WorkerName   NVARCHAR(310);

    SELECT @UserId = [UserId], @WorkDate = [WorkDate]
    FROM dbo.[TimeEntry]
    WHERE [Id] = @TimeEntryId;

    IF @UserId IS NULL
    BEGIN
        RAISERROR('TimeEntry %d not found.', 16, 1, @TimeEntryId);
        RETURN;
    END

    SELECT
        @EmployeeId = [EmployeeId],
        @VendorId   = [VendorId],
        @WorkerName = LTRIM(RTRIM(ISNULL([Firstname], N'') + N' ' + ISNULL([Lastname], N'')))
    FROM dbo.[User]
    WHERE [Id] = @UserId;

    IF @EmployeeId IS NOT NULL AND @VendorId IS NOT NULL
    BEGIN
        RAISERROR('User %d has both EmployeeId and VendorId set (XOR violated).', 16, 1, @UserId);
        RETURN;
    END

    IF @EmployeeId IS NULL AND @VendorId IS NULL
    BEGIN
        RAISERROR(
            'User %d has no worker linkage (User.EmployeeId and VendorId both NULL). Set one via UserProfile before submitting TimeEntries for billing.',
            16, 1, @UserId
        );
        RETURN;
    END

    -- Semi-monthly billing period (decision #3).
    DECLARE @BillingPeriodStart DATE;
    DECLARE @BillingPeriodEnd   DATE;
    IF DAY(@WorkDate) <= 15
    BEGIN
        SET @BillingPeriodStart = DATEFROMPARTS(YEAR(@WorkDate), MONTH(@WorkDate), 1);
        SET @BillingPeriodEnd   = DATEFROMPARTS(YEAR(@WorkDate), MONTH(@WorkDate), 15);
    END
    ELSE
    BEGIN
        SET @BillingPeriodStart = DATEFROMPARTS(YEAR(@WorkDate), MONTH(@WorkDate), 16);
        SET @BillingPeriodEnd   = EOMONTH(@WorkDate);
    END

    -- Per-project buckets.
    DECLARE @Buckets TABLE (
        ProjectId    BIGINT        NULL,
        TotalHours   DECIMAL(6,2)  NOT NULL,
        ConcatNotes  NVARCHAR(MAX) NULL
    );

    INSERT INTO @Buckets (ProjectId, TotalHours, ConcatNotes)
    SELECT
        tl.[ProjectId],
        SUM(ISNULL(tl.[Duration], 0)),
        STRING_AGG(NULLIF(LTRIM(RTRIM(ISNULL(tl.[Note], N''))), N''), N'; ')
            WITHIN GROUP (ORDER BY tl.[ClockIn])
    FROM dbo.[TimeLog] tl
    WHERE tl.[TimeEntryId] = @TimeEntryId
      AND (tl.[LogType] IS NULL OR tl.[LogType] = 'work')
    GROUP BY tl.[ProjectId];

    DECLARE @Results TABLE (
        TargetTable    NVARCHAR(30)  NOT NULL,
        TargetRowId    BIGINT        NULL,
        LineItemRowId  BIGINT        NULL,
        ProjectId      BIGINT        NULL,
        WorkDate       DATE          NOT NULL,
        TotalHours     DECIMAL(6,2)  NOT NULL,
        HourlyRate     DECIMAL(18,4) NULL,
        Markup         DECIMAL(18,4) NULL,
        RateSource     NVARCHAR(20)  NULL,
        Status         NVARCHAR(20)  NOT NULL,
        Note           NVARCHAR(500) NULL
    );

    -- ─── Parent-level aggregates ───────────────────────────────────────────
    DECLARE @BucketCount     INT;
    DECLARE @ParentTotalHrs  DECIMAL(6,2);
    DECLARE @ParentProjectId BIGINT;
    DECLARE @ParentRate      DECIMAL(18,4);
    DECLARE @ParentMarkup    DECIMAL(18,4);
    DECLARE @ParentAmount    DECIMAL(18,2);
    DECLARE @ParentRateSrc   NVARCHAR(20);
    DECLARE @ParentDesc      NVARCHAR(MAX) = NULL;
    DECLARE @ParentNote      NVARCHAR(500) = NULL;

    SELECT
        @BucketCount    = COUNT(*),
        @ParentTotalHrs = SUM(TotalHours)
    FROM @Buckets;

    IF @BucketCount = 0
    BEGIN
        -- No work logs (only breaks, or no logs at all). Nothing to aggregate.
        SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
               CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
               TotalHours, HourlyRate, Markup, RateSource, Status, Note
        FROM @Results;
        RETURN;
    END

    IF @BucketCount = 1
    BEGIN
        SELECT TOP 1 @ParentProjectId = ProjectId FROM @Buckets;

        IF @EmployeeId IS NOT NULL
        BEGIN
            DECLARE @RateE_Parent TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateE_Parent
            EXEC dbo.ReadEffectiveRateForEmployeeProject @EmployeeId = @EmployeeId, @ProjectId = @ParentProjectId;
            SELECT TOP 1 @ParentRate = HourlyRate, @ParentMarkup = Markup, @ParentRateSrc = RateSource FROM @RateE_Parent;
        END
        ELSE
        BEGIN
            DECLARE @RateV_Parent TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateV_Parent
            EXEC dbo.ReadEffectiveRateForVendorProject @VendorId = @VendorId, @ProjectId = @ParentProjectId;
            SELECT TOP 1 @ParentRate = HourlyRate, @ParentMarkup = Markup, @ParentRateSrc = RateSource FROM @RateV_Parent;
        END

        IF @ParentRate IS NOT NULL
        BEGIN
            SET @ParentAmount = @ParentTotalHrs * @ParentRate * (1 + ISNULL(@ParentMarkup, 0));
        END
        ELSE
        BEGIN
            SET @ParentDesc = N'Rate not configured for ' + @WorkerName
                + N' on Project Id=' + ISNULL(CAST(@ParentProjectId AS NVARCHAR(20)), N'(none)')
                + N'. Set a default on the Worker or add a per-project override.';
            SET @ParentNote = N'rate_source=none';
        END
    END
    ELSE
    BEGIN
        -- Multi-project: parent ProjectId / rate / markup / amount are
        -- meaningless aggregates. Leave NULL — the per-project values live
        -- on the line items.
        SET @ParentProjectId = NULL;
        SET @ParentRate      = NULL;
        SET @ParentMarkup    = NULL;
        SET @ParentAmount    = NULL;
        SET @ParentRateSrc   = 'multi_project';
    END

    DECLARE @Status NVARCHAR(20) = 'pending_review';

    -- ─── Parent upsert: ONE row per TimeEntry, keyed on SourceTimeEntryId ──
    DECLARE @ParentRowId BIGINT;

    IF @EmployeeId IS NOT NULL
    BEGIN
        SELECT @ParentRowId = [Id]
        FROM dbo.[EmployeeLabor]
        WHERE [SourceTimeEntryId] = @TimeEntryId;

        IF @ParentRowId IS NULL
        BEGIN
            INSERT INTO dbo.[EmployeeLabor]
                ([CreatedDatetime], [ModifiedDatetime], [EmployeeId], [ProjectId], [WorkDate],
                 [BillingPeriodStart], [BillingPeriodEnd], [TotalHours], [HourlyRate], [Markup],
                 [TotalAmount], [Description], [Status], [SourceTimeEntryId])
            VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @EmployeeId, @ParentProjectId, @WorkDate,
                    @BillingPeriodStart, @BillingPeriodEnd, @ParentTotalHrs, @ParentRate, @ParentMarkup,
                    @ParentAmount, @ParentDesc, @Status, @TimeEntryId);
            SET @ParentRowId = SCOPE_IDENTITY();
        END
        ELSE
        BEGIN
            IF EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [Id] = @ParentRowId AND [Status] = 'invoiced')
            BEGIN
                -- Frozen — already invoiced; surface a note + skip child upserts.
                SET @ParentNote = COALESCE(@ParentNote + N'; ', N'') + N'frozen — already invoiced, skipped';
                INSERT INTO @Results VALUES (N'EmployeeLabor', @ParentRowId, NULL, @ParentProjectId, @WorkDate,
                                             @ParentTotalHrs, @ParentRate, @ParentMarkup, @ParentRateSrc, @Status, @ParentNote);

                SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
                       CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
                       TotalHours, HourlyRate, Markup, RateSource, Status, Note
                FROM @Results;
                RETURN;
            END

            UPDATE dbo.[EmployeeLabor]
            SET [ModifiedDatetime]  = SYSUTCDATETIME(),
                [ProjectId]         = @ParentProjectId,
                [TotalHours]        = @ParentTotalHrs,
                [HourlyRate]        = @ParentRate,
                [Markup]            = @ParentMarkup,
                [TotalAmount]       = @ParentAmount,
                [Description]       = @ParentDesc,
                [BillingPeriodEnd]  = @BillingPeriodEnd,
                [SourceTimeEntryId] = @TimeEntryId
            WHERE [Id] = @ParentRowId;
        END
    END
    ELSE
    BEGIN
        SELECT @ParentRowId = [Id]
        FROM dbo.[ContractLabor]
        WHERE [SourceTimeEntryId] = @TimeEntryId;

        IF @ParentRowId IS NULL
        BEGIN
            INSERT INTO dbo.[ContractLabor]
                ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ProjectId], [WorkDate],
                 [BillingPeriodStart], [TotalHours], [HourlyRate], [Markup], [TotalAmount],
                 [Description], [Status], [BillVendorId], [EmployeeName], [SourceTimeEntryId])
            VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @VendorId, @ParentProjectId, @WorkDate,
                    @BillingPeriodStart, @ParentTotalHrs, @ParentRate, @ParentMarkup, @ParentAmount,
                    @ParentDesc, @Status, @VendorId, @WorkerName, @TimeEntryId);
            SET @ParentRowId = SCOPE_IDENTITY();
        END
        ELSE
        BEGIN
            IF EXISTS (SELECT 1 FROM dbo.[ContractLabor] WHERE [Id] = @ParentRowId AND [Status] = 'billed')
            BEGIN
                SET @ParentNote = COALESCE(@ParentNote + N'; ', N'') + N'frozen — already billed, skipped';
                INSERT INTO @Results VALUES (N'ContractLabor', @ParentRowId, NULL, @ParentProjectId, @WorkDate,
                                             @ParentTotalHrs, @ParentRate, @ParentMarkup, @ParentRateSrc, @Status, @ParentNote);

                SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
                       CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
                       TotalHours, HourlyRate, Markup, RateSource, Status, Note
                FROM @Results;
                RETURN;
            END

            UPDATE dbo.[ContractLabor]
            SET [ModifiedDatetime]  = SYSUTCDATETIME(),
                [ProjectId]         = @ParentProjectId,
                [TotalHours]        = @ParentTotalHrs,
                [HourlyRate]        = @ParentRate,
                [Markup]            = @ParentMarkup,
                [TotalAmount]       = @ParentAmount,
                [Description]       = @ParentDesc,
                [SourceTimeEntryId] = @TimeEntryId
            WHERE [Id] = @ParentRowId;
        END
    END

    -- ─── Per-bucket line-item upserts ──────────────────────────────────────
    DECLARE @ProjectId    BIGINT;
    DECLARE @TotalHours   DECIMAL(6,2);
    DECLARE @ConcatNotes  NVARCHAR(MAX);

    DECLARE bucket_cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT ProjectId, TotalHours, ConcatNotes FROM @Buckets;

    OPEN bucket_cur;
    FETCH NEXT FROM bucket_cur INTO @ProjectId, @TotalHours, @ConcatNotes;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        DECLARE @HourlyRate     DECIMAL(18,4) = NULL;
        DECLARE @Markup         DECIMAL(18,4) = NULL;
        DECLARE @RateSource     NVARCHAR(20)  = 'none';
        DECLARE @TotalAmount    DECIMAL(18,2) = NULL;
        DECLARE @LineItemRowId  BIGINT        = NULL;
        DECLARE @LineNote       NVARCHAR(500) = NULL;

        IF @EmployeeId IS NOT NULL
        BEGIN
            DECLARE @RateE TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateE
            EXEC dbo.ReadEffectiveRateForEmployeeProject @EmployeeId = @EmployeeId, @ProjectId = @ProjectId;
            SELECT TOP 1 @HourlyRate = HourlyRate, @Markup = Markup, @RateSource = RateSource FROM @RateE;
            DELETE FROM @RateE;
        END
        ELSE
        BEGIN
            DECLARE @RateV TABLE (HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, RateSource NVARCHAR(20) NULL);
            INSERT INTO @RateV
            EXEC dbo.ReadEffectiveRateForVendorProject @VendorId = @VendorId, @ProjectId = @ProjectId;
            SELECT TOP 1 @HourlyRate = HourlyRate, @Markup = Markup, @RateSource = RateSource FROM @RateV;
            DELETE FROM @RateV;
        END

        IF @HourlyRate IS NOT NULL
        BEGIN
            SET @TotalAmount = @TotalHours * @HourlyRate * (1 + ISNULL(@Markup, 0));
        END
        ELSE
        BEGIN
            SET @LineNote = N'rate_source=none for Project Id=' + ISNULL(CAST(@ProjectId AS NVARCHAR(20)), N'(none)');
        END

        IF @EmployeeId IS NOT NULL
        BEGIN
            -- Line-item lookup: NULL-defend before SELECT to avoid the
            -- same `SELECT @var = ... WHERE no_match` no-op trap on the
            -- parent. (Belt-and-suspenders — line items are upserted
            -- within a single parent so collision is less likely, but
            -- the bug class is real either way.)
            SET @LineItemRowId = NULL;
            SELECT @LineItemRowId = [Id]
            FROM dbo.[EmployeeLaborLineItem]
            WHERE [EmployeeLaborId]   = @ParentRowId
              AND [SourceTimeEntryId] = @TimeEntryId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId));

            IF @LineItemRowId IS NULL
            BEGIN
                INSERT INTO dbo.[EmployeeLaborLineItem]
                    ([CreatedDatetime], [ModifiedDatetime], [EmployeeLaborId], [LineDate], [ProjectId],
                     [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
                     [IsBillable], [IsOverhead], [SourceTimeEntryId])
                VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @ParentRowId, @WorkDate, @ProjectId,
                        NULL, @ConcatNotes, @TotalHours, @HourlyRate, @Markup, @TotalAmount,
                        1, 0, @TimeEntryId);
                SET @LineItemRowId = SCOPE_IDENTITY();
            END
            ELSE
            BEGIN
                -- Preserve PM edits: SubCostCodeId, Description, IsBillable,
                -- IsOverhead, InvoiceLineItemId all left alone.
                UPDATE dbo.[EmployeeLaborLineItem]
                SET [ModifiedDatetime] = SYSUTCDATETIME(),
                    [Hours]    = @TotalHours,
                    [Rate]     = @HourlyRate,
                    [Markup]   = @Markup,
                    [Price]    = @TotalAmount,
                    [LineDate] = @WorkDate
                WHERE [Id] = @LineItemRowId;
            END

            INSERT INTO @Results VALUES (N'EmployeeLabor', @ParentRowId, @LineItemRowId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @LineNote);
        END
        ELSE
        BEGIN
            SET @LineItemRowId = NULL;
            SELECT @LineItemRowId = [Id]
            FROM dbo.[ContractLaborLineItem]
            WHERE [ContractLaborId]   = @ParentRowId
              AND [SourceTimeEntryId] = @TimeEntryId
              AND ((@ProjectId IS NULL AND [ProjectId] IS NULL) OR ([ProjectId] = @ProjectId));

            IF @LineItemRowId IS NULL
            BEGIN
                INSERT INTO dbo.[ContractLaborLineItem]
                    ([CreatedDatetime], [ModifiedDatetime], [ContractLaborId], [LineDate], [ProjectId],
                     [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
                     [IsBillable], [IsOverhead], [SourceTimeEntryId])
                VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), @ParentRowId, @WorkDate, @ProjectId,
                        NULL, @ConcatNotes, @TotalHours, @HourlyRate, @Markup, @TotalAmount,
                        1, 0, @TimeEntryId);
                SET @LineItemRowId = SCOPE_IDENTITY();
            END
            ELSE
            BEGIN
                UPDATE dbo.[ContractLaborLineItem]
                SET [ModifiedDatetime] = SYSUTCDATETIME(),
                    [Hours]    = @TotalHours,
                    [Rate]     = @HourlyRate,
                    [Markup]   = @Markup,
                    [Price]    = @TotalAmount,
                    [LineDate] = @WorkDate
                WHERE [Id] = @LineItemRowId;
            END

            INSERT INTO @Results VALUES (N'ContractLabor', @ParentRowId, @LineItemRowId, @ProjectId, @WorkDate,
                                         @TotalHours, @HourlyRate, @Markup, @RateSource, @Status, @LineNote);
        END

        FETCH NEXT FROM bucket_cur INTO @ProjectId, @TotalHours, @ConcatNotes;
    END

    CLOSE bucket_cur;
    DEALLOCATE bucket_cur;

    SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
           CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
           TotalHours, HourlyRate, Markup, RateSource, Status, Note
    FROM @Results;
END;
GO



CREATE OR ALTER PROCEDURE dbo.IsTimeEntryDownstreamLocked
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Locked BIT = 0;

    IF EXISTS (
        SELECT 1 FROM dbo.[ContractLabor]
        WHERE [SourceTimeEntryId] = @TimeEntryId
          AND [Status] = 'billed'
    )
        SET @Locked = 1;

    IF @Locked = 0 AND OBJECT_ID('dbo.[EmployeeLabor]', 'U') IS NOT NULL
    BEGIN
        IF EXISTS (
            SELECT 1 FROM dbo.[EmployeeLabor]
            WHERE [SourceTimeEntryId] = @TimeEntryId
              AND [Status] = 'invoiced'
        )
            SET @Locked = 1;
    END

    SELECT @Locked AS Locked;
END;
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

-- =============================================================================
-- 2026-06-16 — Time-Entry daily digest support.
--
-- Powers the morning "here's the time recorded for you yesterday" email each
-- worker receives so they can confirm correctness.
--
--   dbo.ReadTimeEntriesForDigestByWorkDate(@WorkDate)
--        One flat row per (TimeEntry x TimeLog) for a single work_date, joined
--        up to the worker (name + first non-null Contact email), the entry's
--        current status, and each log's Project name. LEFT JOIN TimeLog so an
--        entry with no logs still surfaces (NULL log columns). The digest
--        service groups these by worker in Python. Real humans only — LLM
--        agents (User.IsAgent=1) and persona test accounts (Auth.Username
--        'persona_*') are excluded, matching the review-recipient resolvers
--        (see review/sql/migrations/008_filter_personas_from_review_recipients).
--        Runs in system context (drain-secret admin endpoint) — no per-user
--        row scoping; it reads across all workers by design.
--
-- The digest's outbox idempotency helper (dbo.CountMsOutboxByEntity) is homed
-- with the MS outbox package: integrations/ms/outbox/sql/ms.outbox.sql.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Digest resolver — entries + logs + worker email + project + status
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ReadTimeEntriesForDigestByWorkDate
(
    @WorkDate DATE
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        te.[Id]                                   AS [TimeEntryId],
        te.[PublicId]                             AS [TimeEntryPublicId],
        CONVERT(VARCHAR(10), te.[WorkDate], 120)  AS [WorkDate],
        te.[Note]                                 AS [EntryNote],
        u.[Id]                                    AS [UserId],
        u.[PublicId]                              AS [UserPublicId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        cs.[Status]                               AS [CurrentStatus],
        tl.[Id]                                   AS [TimeLogId],
        tl.[PublicId]                             AS [TimeLogPublicId],
        CONVERT(VARCHAR(23), tl.[ClockIn], 121)   AS [ClockIn],
        CONVERT(VARCHAR(23), tl.[ClockOut], 121)  AS [ClockOut],
        tl.[LogType],
        tl.[Duration],
        tl.[ProjectId],
        p.[Name]                                  AS [ProjectName],
        p.[Abbreviation]                          AS [ProjectAbbreviation],
        tl.[Note]                                 AS [LogNote]
    FROM dbo.[TimeEntry] te
    INNER JOIN dbo.[User] u ON u.[Id] = te.[UserId]
    LEFT JOIN UserEmails ue
        ON ue.[UserId] = u.[Id]
       AND ue.rn = 1
    OUTER APPLY (
        SELECT TOP 1 s.[Status]
        FROM dbo.[TimeEntryStatus] s
        WHERE s.[TimeEntryId] = te.[Id]
        ORDER BY s.[CreatedDatetime] DESC
    ) cs
    LEFT JOIN dbo.[TimeLog] tl ON tl.[TimeEntryId] = te.[Id]
    LEFT JOIN dbo.[Project] p  ON p.[Id] = tl.[ProjectId]
    WHERE te.[WorkDate] = @WorkDate
      -- Real humans only: exclude LLM agent accounts (User.IsAgent = 1)
      -- and persona test accounts (Auth.Username starting with 'persona_',
      -- whitespace-tolerant) — same filter as the review resolvers.
      AND NOT EXISTS (
          SELECT 1 FROM dbo.[User] ua
          WHERE ua.[Id] = te.[UserId]
            AND ua.[IsAgent] = 1
      )
      AND NOT EXISTS (
          SELECT 1 FROM dbo.[Auth] a
          WHERE a.[UserId] = te.[UserId]
            AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
      )
    ORDER BY u.[Lastname], u.[Firstname], te.[Id], tl.[ClockIn];
END;
GO



CREATE OR ALTER PROCEDURE dbo.ReadTimeEntryBilledLineage
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Vendor path: ContractLabor → BillLineItem → Bill
    SELECT
        N'ContractLabor'                          AS TargetTable,
        cl.[Id]                                   AS TargetId,
        CAST(cl.[PublicId] AS NVARCHAR(36))       AS TargetPublicId,
        cl.[Status]                               AS LaborStatus,
        CONVERT(VARCHAR(10), cl.[WorkDate], 120)  AS WorkDate,
        cl.[VendorId]                             AS WorkerId,           -- VendorId for this row
        v.[Name]                                  AS WorkerName,
        cl.[TotalAmount]                          AS TotalAmount,
        b.[Id]                                    AS LinkedTargetId,     -- Bill.Id when billed
        CAST(b.[PublicId] AS NVARCHAR(36))        AS LinkedTargetPublicId,
        N'Bill'                                   AS LinkedTargetTable,
        b.[BillNumber]                            AS LinkedTargetNumber
    FROM dbo.[ContractLabor] cl
    LEFT JOIN dbo.[Vendor]       v   ON v.[Id]   = cl.[VendorId]
    LEFT JOIN dbo.[BillLineItem] bli ON bli.[Id] = cl.[BillLineItemId]
    LEFT JOIN dbo.[Bill]         b   ON b.[Id]   = bli.[BillId]
    WHERE cl.[SourceTimeEntryId] = @TimeEntryId

    UNION ALL

    -- Employee path: EmployeeLabor → InvoiceLineItem → Invoice
    SELECT
        N'EmployeeLabor'                          AS TargetTable,
        el.[Id]                                   AS TargetId,
        CAST(el.[PublicId] AS NVARCHAR(36))       AS TargetPublicId,
        el.[Status]                               AS LaborStatus,
        CONVERT(VARCHAR(10), el.[WorkDate], 120)  AS WorkDate,
        el.[EmployeeId]                           AS WorkerId,           -- EmployeeId for this row
        e.[Firstname] + ' ' + e.[Lastname]        AS WorkerName,
        el.[TotalAmount]                          AS TotalAmount,
        i.[Id]                                    AS LinkedTargetId,
        CAST(i.[PublicId] AS NVARCHAR(36))        AS LinkedTargetPublicId,
        N'Invoice'                                AS LinkedTargetTable,
        i.[InvoiceNumber]                         AS LinkedTargetNumber
    FROM dbo.[EmployeeLabor] el
    LEFT JOIN dbo.[Employee]        e   ON e.[Id]   = el.[EmployeeId]
    LEFT JOIN dbo.[InvoiceLineItem] ili ON ili.[Id] = el.[InvoiceLineItemId]
    LEFT JOIN dbo.[Invoice]         i   ON i.[Id]   = ili.[InvoiceId]
    WHERE el.[SourceTimeEntryId] = @TimeEntryId

    ORDER BY WorkDate, TargetTable;
END;
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

-- StampTimeEntryReview — set ReviewPriority + ReviewReasons on a TimeEntry.
--
-- Called by the time_tracking_specialist agent's flag tool to record its
-- bucketing decision. Does NOT transition CurrentStatus and does NOT write
-- a Workflow / WorkflowEvent row — flag metadata is observability, not a
-- state transition. (Decision: 2026-05-26 refinement, see
-- project_time_tracking_specialist.md.)
--
-- ModifiedDatetime is intentionally NOT touched. CRUD activity on the entry
-- itself is what should bump ModifiedDatetime; an automated review stamp is
-- a sidecar.
--
-- @ReasonsJson is opaque to this sproc — caller passes a JSON string,
-- typically a short-code array like '["null_project","over_12hr"]', or '[]'
-- for clean entries. No JSON validation here; the agent is the producer.
--
-- Idempotent (UPDATE by PublicId; safe to re-run with the same payload).

CREATE OR ALTER PROCEDURE [dbo].[StampTimeEntryReview]
(
    @TimeEntryPublicId UNIQUEIDENTIFIER,
    @Priority          VARCHAR(20),
    @ReasonsJson       NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [dbo].[TimeEntry]
    SET [ReviewPriority] = @Priority,
        [ReviewReasons]  = @ReasonsJson
    WHERE [PublicId] = @TimeEntryPublicId;

    SELECT @@ROWCOUNT AS [AffectedRowCount];
END;
GO

