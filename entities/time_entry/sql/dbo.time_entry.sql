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

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.TimeEntry') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[TimeEntry] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_TimeEntry_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_TimeEntry_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[TimeEntry] ADD CONSTRAINT [FK_TimeEntry_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
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

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.TimeLog') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_TimeLog_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_TimeLog_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD CONSTRAINT [FK_TimeLog_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
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
        [Note],
        [ReviewPriority],
        [ReviewReasons]
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
        [Note],
        [ReviewPriority],
        [ReviewReasons]
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
        [Note],
        [ReviewPriority],
        [ReviewReasons]
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
        [Note],
        [ReviewPriority],
        [ReviewReasons]
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
        te.[Note],
        te.[ReviewPriority],
        te.[ReviewReasons]
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
        te.[Note],
        te.[ReviewPriority],
        te.[ReviewReasons]
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
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    -- U-596: the header may only change while the day is in 'draft' (or has no
    -- status history). Checked HERE, in the write's own transaction, under the
    -- range lock every transition, submit and log write take — the service's
    -- own draft check is a read, and a submit or approval can land between it
    -- and this write (submission does not touch this row's RowVersion).
    DECLARE @CurrentStatus NVARCHAR(20) = (
        SELECT TOP 1 s.[Status] FROM dbo.[TimeEntryStatus] s WITH (UPDLOCK, HOLDLOCK)
        WHERE s.[TimeEntryId] = @Id
        ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC);
    IF @CurrentStatus IS NOT NULL AND @CurrentStatus <> 'draft'
    BEGIN
        COMMIT TRANSACTION;      -- nothing written yet: commit, then raise (never ROLLBACK — error 266 under pyodbc)
        DECLARE @Locked NVARCHAR(400) = N'Cannot modify a time entry when it is in ''' + @CurrentStatus
            + N''' status — the entry is not in ''draft''.';
        RAISERROR(@Locked, 16, 1);
        RETURN;
    END

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
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    -- U-596: the header may only change while the day is in 'draft' (or has no
    -- status history). Checked HERE, in the write's own transaction, under the
    -- range lock every transition, submit and log write take — the service's
    -- own draft check is a read, and a submit or approval can land between it
    -- and this write (submission does not touch this row's RowVersion).
    DECLARE @CurrentStatus NVARCHAR(20) = (
        SELECT TOP 1 s.[Status] FROM dbo.[TimeEntryStatus] s WITH (UPDLOCK, HOLDLOCK)
        WHERE s.[TimeEntryId] = @Id
        ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC);
    IF @CurrentStatus IS NOT NULL AND @CurrentStatus <> 'draft'
    BEGIN
        COMMIT TRANSACTION;      -- nothing written yet: commit, then raise (never ROLLBACK — error 266 under pyodbc)
        DECLARE @Locked NVARCHAR(400) = N'Cannot modify a time entry when it is in ''' + @CurrentStatus
            + N''' status — the entry is not in ''draft''.';
        RAISERROR(@Locked, 16, 1);
        RETURN;
    END

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
    @CreatedByUserId BIGINT = NULL,
    @ReopenAsUserId BIGINT = NULL,     -- U-596: the OWNER, when the service decided a submitted day may reopen
    @ReopenNote NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;      -- DML now precedes the OUTPUT rows (the reopen): no row-count chatter before them
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    -- U-596: a log may only change while its day is in 'draft' (or has no
    -- status history). Checked HERE, in the write's own transaction, under a
    -- range lock on the day's status rows — every transition and the submit
    -- take the same lock, so nothing lands between this read and the write.
    -- A 'submitted' day is REOPENED here, in this same transaction, when the
    -- service passes the OWNER as @ReopenAsUserId (it decided eligibility:
    -- untouched labor, no stale row, no bucket change…): a draft status row
    -- plus the review marker, then the write. If the write then fails — a bad
    -- project id, a unique-key collision — XACT_ABORT rolls the reopen back
    -- with it: a refused request never leaves a reopened day behind.
    DECLARE @CurrentStatus NVARCHAR(20) = (
        SELECT TOP 1 s.[Status] FROM dbo.[TimeEntryStatus] s WITH (UPDLOCK, HOLDLOCK)
        WHERE s.[TimeEntryId] = @TimeEntryId
        ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC);
    IF @CurrentStatus = 'submitted' AND @ReopenAsUserId IS NOT NULL
       AND EXISTS (SELECT 1 FROM dbo.[TimeEntry] te WHERE te.[Id] = @TimeEntryId AND te.[UserId] = @ReopenAsUserId)
    BEGIN
        -- The service checked the labor before calling; re-check it HERE, under
        -- the locks the predicate takes, so a decision or edit that landed in
        -- between refuses the reopen instead of reopening a day whose labor
        -- can no longer be rebuilt.
        DECLARE @LaborUntouched BIT = 1;
        EXEC dbo.IsTimeEntryLaborUntouched @TimeEntryId = @TimeEntryId, @Untouched = @LaborUntouched OUTPUT, @ReturnRow = 0;
        IF @LaborUntouched = 0
        BEGIN
            COMMIT TRANSACTION;      -- nothing written yet; see the refusal below
            RAISERROR('Cannot modify time logs when time entry is in ''submitted'' status — the entry is not in ''draft''. Its labor has been reviewed, billed or invoiced; reverse that first.', 16, 1);
            RETURN;
        END
        INSERT INTO dbo.[TimeEntryStatus] ([CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note])
        VALUES (SYSUTCDATETIME(), @TimeEntryId, N'draft', @ReopenAsUserId, @ReopenNote);
        DECLARE @ReopenedPublicId UNIQUEIDENTIFIER = (SELECT [PublicId] FROM dbo.[TimeEntry] WHERE [Id] = @TimeEntryId);
        EXEC dbo.StampTimeEntryReview @TimeEntryPublicId = @ReopenedPublicId, @Priority = 'medium',
                                      @ReasonsJson = N'["reopened_after_submit"]', @ReturnRow = 0;
        SET @CurrentStatus = 'draft';
    END
    IF @CurrentStatus IS NOT NULL AND @CurrentStatus <> 'draft'
    BEGIN
        -- COMMIT then RAISERROR, never ROLLBACK: nothing has been written, and
        -- pyodbc runs autocommit-off — a ROLLBACK here would zero the implicit
        -- outer transaction and surface error 266 instead of this refusal.
        COMMIT TRANSACTION;
        DECLARE @Locked NVARCHAR(400) = N'Cannot modify time logs when time entry is in ''' + @CurrentStatus
            + N''' status — the entry is not in ''draft''.';
        RAISERROR(@Locked, 16, 1);
        RETURN;
    END

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
    @ActorCanViewTeam BIT = 0,
    @ReopenAsUserId BIGINT = NULL,     -- U-596: the OWNER, when the service decided a submitted day may reopen
    @ReopenNote NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;      -- DML now precedes the OUTPUT rows (the reopen): no row-count chatter before them
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DECLARE @TimeEntryId BIGINT = (SELECT [TimeEntryId] FROM dbo.[TimeLog] WHERE [Id] = @Id);
    -- U-596: a log may only change while its day is in 'draft' (or has no
    -- status history). Checked HERE, in the write's own transaction, under a
    -- range lock on the day's status rows — every transition and the submit
    -- take the same lock, so nothing lands between this read and the write.
    -- A 'submitted' day is REOPENED here, in this same transaction, when the
    -- service passes the OWNER as @ReopenAsUserId (it decided eligibility:
    -- untouched labor, no stale row, no bucket change…): a draft status row
    -- plus the review marker, then the write. If the write then fails — a bad
    -- project id, a unique-key collision — XACT_ABORT rolls the reopen back
    -- with it: a refused request never leaves a reopened day behind.
    DECLARE @CurrentStatus NVARCHAR(20) = (
        SELECT TOP 1 s.[Status] FROM dbo.[TimeEntryStatus] s WITH (UPDLOCK, HOLDLOCK)
        WHERE s.[TimeEntryId] = @TimeEntryId
        ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC);
    IF @CurrentStatus = 'submitted' AND @ReopenAsUserId IS NOT NULL
       AND EXISTS (SELECT 1 FROM dbo.[TimeEntry] te WHERE te.[Id] = @TimeEntryId AND te.[UserId] = @ReopenAsUserId)
    BEGIN
        -- The service checked the labor before calling; re-check it HERE, under
        -- the locks the predicate takes, so a decision or edit that landed in
        -- between refuses the reopen instead of reopening a day whose labor
        -- can no longer be rebuilt.
        DECLARE @LaborUntouched BIT = 1;
        EXEC dbo.IsTimeEntryLaborUntouched @TimeEntryId = @TimeEntryId, @Untouched = @LaborUntouched OUTPUT, @ReturnRow = 0;
        IF @LaborUntouched = 0
        BEGIN
            COMMIT TRANSACTION;      -- nothing written yet; see the refusal below
            RAISERROR('Cannot modify time logs when time entry is in ''submitted'' status — the entry is not in ''draft''. Its labor has been reviewed, billed or invoiced; reverse that first.', 16, 1);
            RETURN;
        END
        INSERT INTO dbo.[TimeEntryStatus] ([CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note])
        VALUES (SYSUTCDATETIME(), @TimeEntryId, N'draft', @ReopenAsUserId, @ReopenNote);
        DECLARE @ReopenedPublicId UNIQUEIDENTIFIER = (SELECT [PublicId] FROM dbo.[TimeEntry] WHERE [Id] = @TimeEntryId);
        EXEC dbo.StampTimeEntryReview @TimeEntryPublicId = @ReopenedPublicId, @Priority = 'medium',
                                      @ReasonsJson = N'["reopened_after_submit"]', @ReturnRow = 0;
        SET @CurrentStatus = 'draft';
    END
    IF @CurrentStatus IS NOT NULL AND @CurrentStatus <> 'draft'
    BEGIN
        -- COMMIT then RAISERROR, never ROLLBACK: nothing has been written, and
        -- pyodbc runs autocommit-off — a ROLLBACK here would zero the implicit
        -- outer transaction and surface error 266 instead of this refusal.
        COMMIT TRANSACTION;
        DECLARE @Locked NVARCHAR(400) = N'Cannot modify time logs when time entry is in ''' + @CurrentStatus
            + N''' status — the entry is not in ''draft''.';
        RAISERROR(@Locked, 16, 1);
        RETURN;
    END

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
    @ActorCanViewTeam BIT = 0,
    @ReopenAsUserId BIGINT = NULL,     -- U-596: the OWNER, when the service decided a submitted day may reopen
    @ReopenNote NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;      -- DML now precedes the OUTPUT rows (the reopen): no row-count chatter before them
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    DECLARE @TimeEntryId BIGINT = (SELECT [TimeEntryId] FROM dbo.[TimeLog] WHERE [Id] = @Id);
    -- U-596: a log may only change while its day is in 'draft' (or has no
    -- status history). Checked HERE, in the write's own transaction, under a
    -- range lock on the day's status rows — every transition and the submit
    -- take the same lock, so nothing lands between this read and the write.
    -- A 'submitted' day is REOPENED here, in this same transaction, when the
    -- service passes the OWNER as @ReopenAsUserId (it decided eligibility:
    -- untouched labor, no stale row, no bucket change…): a draft status row
    -- plus the review marker, then the write. If the write then fails — a bad
    -- project id, a unique-key collision — XACT_ABORT rolls the reopen back
    -- with it: a refused request never leaves a reopened day behind.
    DECLARE @CurrentStatus NVARCHAR(20) = (
        SELECT TOP 1 s.[Status] FROM dbo.[TimeEntryStatus] s WITH (UPDLOCK, HOLDLOCK)
        WHERE s.[TimeEntryId] = @TimeEntryId
        ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC);
    IF @CurrentStatus = 'submitted' AND @ReopenAsUserId IS NOT NULL
       AND EXISTS (SELECT 1 FROM dbo.[TimeEntry] te WHERE te.[Id] = @TimeEntryId AND te.[UserId] = @ReopenAsUserId)
    BEGIN
        -- The service checked the labor before calling; re-check it HERE, under
        -- the locks the predicate takes, so a decision or edit that landed in
        -- between refuses the reopen instead of reopening a day whose labor
        -- can no longer be rebuilt.
        DECLARE @LaborUntouched BIT = 1;
        EXEC dbo.IsTimeEntryLaborUntouched @TimeEntryId = @TimeEntryId, @Untouched = @LaborUntouched OUTPUT, @ReturnRow = 0;
        IF @LaborUntouched = 0
        BEGIN
            COMMIT TRANSACTION;      -- nothing written yet; see the refusal below
            RAISERROR('Cannot modify time logs when time entry is in ''submitted'' status — the entry is not in ''draft''. Its labor has been reviewed, billed or invoiced; reverse that first.', 16, 1);
            RETURN;
        END
        INSERT INTO dbo.[TimeEntryStatus] ([CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note])
        VALUES (SYSUTCDATETIME(), @TimeEntryId, N'draft', @ReopenAsUserId, @ReopenNote);
        DECLARE @ReopenedPublicId UNIQUEIDENTIFIER = (SELECT [PublicId] FROM dbo.[TimeEntry] WHERE [Id] = @TimeEntryId);
        EXEC dbo.StampTimeEntryReview @TimeEntryPublicId = @ReopenedPublicId, @Priority = 'medium',
                                      @ReasonsJson = N'["reopened_after_submit"]', @ReturnRow = 0;
        SET @CurrentStatus = 'draft';
    END
    IF @CurrentStatus IS NOT NULL AND @CurrentStatus <> 'draft'
    BEGIN
        -- COMMIT then RAISERROR, never ROLLBACK: nothing has been written, and
        -- pyodbc runs autocommit-off — a ROLLBACK here would zero the implicit
        -- outer transaction and surface error 266 instead of this refusal.
        COMMIT TRANSACTION;
        DECLARE @Locked NVARCHAR(400) = N'Cannot modify time logs when time entry is in ''' + @CurrentStatus
            + N''' status — the entry is not in ''draft''.';
        RAISERROR(@Locked, 16, 1);
        RETURN;
    END

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
    -- DECIMAL(25,2) not (18,2) target: large cost * negative markup must not overflow
    -- before markup shrinks it; *(1+markup) stays DECIMAL(38,6), still exact.
    DECLARE @ParentCostAmount DECIMAL(25,2);
    DECLARE @ParentRateSrc   NVARCHAR(20);
    DECLARE @ParentDesc      NVARCHAR(MAX) = NULL;
    DECLARE @ParentNote      NVARCHAR(500) = NULL;

    SELECT
        @BucketCount    = COUNT(*),
        @ParentTotalHrs = SUM(TotalHours)
    FROM @Buckets;

    -- ── U-596: one invariant for a RE-submit ───────────────────────────────
    -- A labor row aggregated from this entry is rebuilt from the logs only while
    -- it is still a pure derivation of them (dbo.IsTimeEntryLaborUntouched: status
    -- 'pending_review', no Review row, no bill/invoice line on the parent or any
    -- line, no PM-split line). Once anyone has reviewed, split, billed or invoiced
    -- it, NOTHING here may change it: a resubmit whose buckets still equal the
    -- lines this entry produced is a no-op; one that would change them is
    -- REFUSED (the office reverses the downstream work first). This is what used
    -- to let a re-submit rewrite a reviewed row's hours, restore a PM's split, or
    -- re-price a billed line — on the reject path as much as on the reopen path.
    -- The office deleted this entry's labor outright: no parent remains to carry
    -- a marker, so the record lives on the entry (LaborDeletedDatetime). Read it
    -- here, independently of whether a parent exists — otherwise a resubmit
    -- would simply recreate the deleted charges.
    IF EXISTS (SELECT 1 FROM dbo.[TimeEntry] te WITH (UPDLOCK, HOLDLOCK)
               WHERE te.[Id] = @TimeEntryId AND te.[LaborDeletedDatetime] IS NOT NULL)
    BEGIN
        DECLARE @RefusedDeleted NVARCHAR(400) = N'REFUSED: TimeEntry ' + CAST(@TimeEntryId AS NVARCHAR(20))
            + N' — the office deleted its labor; it is not rebuilt from the logs without the office.';
        RAISERROR(@RefusedDeleted, 16, 1);
        RETURN;
    END
    DECLARE @ExistingParentId BIGINT = NULL;
    IF @EmployeeId IS NOT NULL
        SELECT @ExistingParentId = [Id] FROM dbo.[EmployeeLabor] WHERE [SourceTimeEntryId] = @TimeEntryId;
    ELSE
        SELECT @ExistingParentId = [Id] FROM dbo.[ContractLabor] WHERE [SourceTimeEntryId] = @TimeEntryId;
    -- A parent in the OTHER family means the entry's worker changed type (or was
    -- reassigned across vendor/employee) since it was aggregated. Rebuilding
    -- here would leave that parent standing beside a new one — double labor —
    -- and it is not this run's to delete. Refuse; the office reconciles.
    IF (@EmployeeId IS NOT NULL AND EXISTS (SELECT 1 FROM dbo.[ContractLabor] WHERE [SourceTimeEntryId] = @TimeEntryId))
       OR (@EmployeeId IS NULL AND OBJECT_ID('dbo.[EmployeeLabor]', 'U') IS NOT NULL
           AND EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [SourceTimeEntryId] = @TimeEntryId))
    BEGIN
        DECLARE @RefusedFamily NVARCHAR(400) = N'REFUSED: TimeEntry ' + CAST(@TimeEntryId AS NVARCHAR(20))
            + N' — its labor was aggregated for a different worker type; reconcile that labor row first.';
        RAISERROR(@RefusedFamily, 16, 1);
        RETURN;
    END
    DECLARE @Untouched BIT = 1;
    IF @ExistingParentId IS NOT NULL
        EXEC dbo.IsTimeEntryLaborUntouched @TimeEntryId = @TimeEntryId, @Untouched = @Untouched OUTPUT, @ReturnRow = 0;
    IF @ExistingParentId IS NOT NULL AND @Untouched = 0
    BEGIN
        DECLARE @Differs BIT = 0;
        IF @EmployeeId IS NOT NULL
        BEGIN
            IF EXISTS (SELECT ProjectId, TotalHours FROM @Buckets
                       EXCEPT SELECT [ProjectId], [Hours] FROM dbo.[EmployeeLaborLineItem]
                              WHERE [EmployeeLaborId] = @ExistingParentId AND [SourceTimeEntryId] = @TimeEntryId)
               OR EXISTS (SELECT [ProjectId], [Hours] FROM dbo.[EmployeeLaborLineItem]
                          WHERE [EmployeeLaborId] = @ExistingParentId AND [SourceTimeEntryId] = @TimeEntryId
                          EXCEPT SELECT ProjectId, TotalHours FROM @Buckets)
                SET @Differs = 1;
        END
        ELSE
        BEGIN
            IF EXISTS (SELECT ProjectId, TotalHours FROM @Buckets
                       EXCEPT SELECT [ProjectId], [Hours] FROM dbo.[ContractLaborLineItem]
                              WHERE [ContractLaborId] = @ExistingParentId AND [SourceTimeEntryId] = @TimeEntryId)
               OR EXISTS (SELECT [ProjectId], [Hours] FROM dbo.[ContractLaborLineItem]
                          WHERE [ContractLaborId] = @ExistingParentId AND [SourceTimeEntryId] = @TimeEntryId
                          EXCEPT SELECT ProjectId, TotalHours FROM @Buckets)
                SET @Differs = 1;
        END
        -- Touched labor is pinned to its date too: a reopened day whose WorkDate
        -- moved cannot be resubmitted "unchanged" with its labor on the old date.
        -- …and to its worker: a reopened day reassigned within the same family
        -- cannot resubmit "unchanged" with labor still on the previous payee.
        IF @Differs = 0 AND (
               (@EmployeeId IS NOT NULL AND EXISTS (SELECT 1 FROM dbo.[EmployeeLabor]
                                                   WHERE [Id] = @ExistingParentId AND ([WorkDate] <> @WorkDate OR [EmployeeId] <> @EmployeeId)))
            OR (@EmployeeId IS NULL AND EXISTS (SELECT 1 FROM dbo.[ContractLabor]
                                                WHERE [Id] = @ExistingParentId AND ([WorkDate] <> @WorkDate OR [VendorId] <> @VendorId))))
            SET @Differs = 1;
        IF @Differs = 1
        BEGIN
            DECLARE @Refused NVARCHAR(400) = N'REFUSED: TimeEntry ' + CAST(@TimeEntryId AS NVARCHAR(20))
                + N' — this resubmit would change labor that has been reviewed, split, billed or invoiced. Reverse that first.';
            RAISERROR(@Refused, 16, 1);
            RETURN;
        END
        -- Identical: nothing to rebuild and nothing may be touched. Report the
        -- existing lines and leave.
        IF @EmployeeId IS NOT NULL
            INSERT INTO @Results
            SELECT N'EmployeeLabor', @ExistingParentId, li.[Id], li.[ProjectId], @WorkDate,
                   li.[Hours], li.[Rate], li.[Markup], N'unchanged', p.[Status], N'unchanged — labor already reviewed/billed; left as is'
            FROM dbo.[EmployeeLaborLineItem] li JOIN dbo.[EmployeeLabor] p ON p.[Id] = li.[EmployeeLaborId]
            WHERE li.[EmployeeLaborId] = @ExistingParentId AND li.[SourceTimeEntryId] = @TimeEntryId;
        ELSE
            INSERT INTO @Results
            SELECT N'ContractLabor', @ExistingParentId, li.[Id], li.[ProjectId], @WorkDate,
                   li.[Hours], li.[Rate], li.[Markup], N'unchanged', p.[Status], N'unchanged — labor already reviewed/billed; left as is'
            FROM dbo.[ContractLaborLineItem] li JOIN dbo.[ContractLabor] p ON p.[Id] = li.[ContractLaborId]
            WHERE li.[ContractLaborId] = @ExistingParentId AND li.[SourceTimeEntryId] = @TimeEntryId;
        SELECT TargetTable, TargetRowId, LineItemRowId, ProjectId,
               CONVERT(VARCHAR(10), WorkDate, 120) AS WorkDate,
               TotalHours, HourlyRate, Markup, RateSource, Status, Note
        FROM @Results;
        RETURN;
    END

    IF @BucketCount = 0
    BEGIN
        -- No work logs (only breaks, or no logs at all). Nothing to aggregate —
        -- and an UNTOUCHED aggregation from an earlier submit must not keep
        -- billing hours the day no longer has (U-596): drop its lines.
        IF @ExistingParentId IS NOT NULL
        BEGIN
            IF @EmployeeId IS NOT NULL
            BEGIN
                DELETE FROM dbo.[EmployeeLaborLineItem] WHERE [EmployeeLaborId] = @ExistingParentId AND [SourceTimeEntryId] = @TimeEntryId;
                UPDATE dbo.[EmployeeLabor] SET [TotalHours] = 0, [TotalAmount] = 0, [ModifiedDatetime] = SYSUTCDATETIME()
                WHERE [Id] = @ExistingParentId;
            END
            ELSE
            BEGIN
                DELETE FROM dbo.[ContractLaborLineItem] WHERE [ContractLaborId] = @ExistingParentId AND [SourceTimeEntryId] = @TimeEntryId;
                EXEC dbo.UpdateContractLaborAggregates @Id = @ExistingParentId, @ReturnRow = 0;
            END
        END
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
            -- Two-shot cent rounding matches web shared/money roundMoney (half away from
            -- zero). Single-shot rounds once at DECIMAL(38,6) and can differ by 1 cent.
            SET @ParentCostAmount = ROUND(@ParentTotalHrs * @ParentRate, 2);
            SET @ParentAmount     = ROUND(@ParentCostAmount * (1 + ISNULL(@ParentMarkup, 0)), 2);
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
        -- meaningless as BUCKET aggregates. Leave NULL — the per-project
        -- values live on the line items.
        -- U-424: on the update path the tail recompute then re-derives the
        -- money columns from those line items, so a multi-project parent
        -- ends up with SUM(Price) and a billable weighted-average rate
        -- rather than NULL. Only the INSERT path persists these NULLs, and
        -- only until its first recompute.
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
                [EmployeeId]        = @EmployeeId,          -- U-596: the day's worker may have changed while reopened
                [ProjectId]         = @ParentProjectId,
                [WorkDate]          = @WorkDate,            -- U-596: the day may have moved while reopened
                [BillingPeriodStart] = @BillingPeriodStart,
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

            -- TotalHours / HourlyRate / Markup / TotalAmount are deliberately
            -- NOT set here (U-424). The recompute at the tail of this
            -- procedure is their sole writer on the update path; assigning
            -- them here too would be a dead store and a second ROWVERSION
            -- bump on the same row, invalidating a client's optimistic-
            -- concurrency token twice per submit. The INSERT branch above
            -- still needs them — it has no row to recompute from yet.
            UPDATE dbo.[ContractLabor]
            SET [ModifiedDatetime]  = SYSUTCDATETIME(),
                [VendorId]          = @VendorId,            -- U-596: the day's worker may have changed while reopened
                [BillVendorId]      = @VendorId,
                [EmployeeName]      = @WorkerName,
                [ProjectId]         = @ParentProjectId,
                [WorkDate]          = @WorkDate,            -- U-596: the day may have moved while reopened
                [BillingPeriodStart] = @BillingPeriodStart,
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
        -- DECIMAL(25,2) not (18,2) target: large cost * negative markup must not overflow
        -- before markup shrinks it; *(1+markup) stays DECIMAL(38,6), still exact.
        DECLARE @CostAmount     DECIMAL(25,2) = NULL;
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
            -- Two-shot cent rounding matches web shared/money roundMoney (half away from
            -- zero). Single-shot rounds once at DECIMAL(38,6) and can differ by 1 cent.
            SET @CostAmount  = ROUND(@TotalHours * @HourlyRate, 2);
            SET @TotalAmount = ROUND(@CostAmount * (1 + ISNULL(@Markup, 0)), 2);
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

    -- ─── Parent aggregates: children are authoritative (U-424) ─────────────
    -- Sole writer of the parent's money columns on the update path. Why
    -- sum-of-children is the one parent semantic: see the U-424 block on
    -- dbo.UpdateContractLaborAggregates in dbo.contract_labor.sql.
    --
    -- What lives only here — why this sproc's own bucket math was not enough:
    -- lines a PM splits off in PUT /{id}/bill carry SourceTimeEntryId = NULL
    -- (CreateContractLaborLineItem takes no such param), so a re-submit
    -- rewrites only the original line yet stamped the parent with this
    -- TimeEntry's bucket totals — leaving TotalAmount at one line's worth
    -- while the children summed to more. That is the prod drift behind U-424.
    --
    -- Consequence worth knowing: Markup becomes the EFFECTIVE fraction implied
    -- by the rounded child prices, so it can differ from the configured rate in
    -- the 4th decimal. It is display-only; the line items carry the rate billed.
    --
    -- @ReturnRow = 0 suppresses the sproc's row set (see its header).
    -- EmployeeLabor has no equivalent recompute sproc — see TODO.md.
    -- U-596: retire lines this entry no longer produces. The loop above upserts
    -- one line per (parent, ProjectId) from the CURRENT work logs; a line whose
    -- project no longer appears (a log moved, deleted, or re-typed as a break)
    -- used to stand forever, so a resubmit billed hours twice. We only reach
    -- this point for an UNTOUCHED aggregation (above), so every such line is
    -- this entry's own and unlinked; the predicates restate that as a guard.
    IF @EmployeeId IS NOT NULL
        DELETE li FROM dbo.[EmployeeLaborLineItem] li
        WHERE li.[EmployeeLaborId] = @ParentRowId AND li.[SourceTimeEntryId] = @TimeEntryId
          AND li.[InvoiceLineItemId] IS NULL
          AND NOT EXISTS (SELECT 1 FROM @Buckets b
                          WHERE (b.ProjectId IS NULL AND li.[ProjectId] IS NULL) OR b.ProjectId = li.[ProjectId]);
    ELSE
        DELETE li FROM dbo.[ContractLaborLineItem] li
        WHERE li.[ContractLaborId] = @ParentRowId AND li.[SourceTimeEntryId] = @TimeEntryId
          AND li.[BillLineItemId] IS NULL
          AND NOT EXISTS (SELECT 1 FROM @Buckets b
                          WHERE (b.ProjectId IS NULL AND li.[ProjectId] IS NULL) OR b.ProjectId = li.[ProjectId]);

    IF @EmployeeId IS NULL AND @ParentRowId IS NOT NULL
    BEGIN
        EXEC dbo.UpdateContractLaborAggregates @Id = @ParentRowId, @ReturnRow = 0;
    END

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
        ORDER BY s.[CreatedDatetime] DESC, s.[Id] DESC
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
    @ReasonsJson       NVARCHAR(MAX),
    @ReturnRow         BIT = 1          -- 0 when called from another sproc: no result set
)
AS
BEGIN
    SET NOCOUNT ON;

    -- U-596: `reopened_after_submit` is a HISTORICAL marker — the worker's own
    -- device reopened a day auto-submit had already submitted. Every later
    -- restamp (the sweep's completeness reasons, the specialist's clean/[])
    -- recomputes reasons from the logs and would erase it, so it is carried
    -- forward here, and a 'clean' or 'low' restamp of such a day lands as 'medium':
    -- a human must still look before approval. Non-JSON input keeps the old
    -- overwrite semantics exactly.
    DECLARE @Marker NVARCHAR(40) = N'reopened_after_submit';
    UPDATE te
    SET [ReviewPriority] = CASE WHEN k.[Marked] = 1 AND @Priority IN ('clean', 'low') THEN 'medium' ELSE @Priority END,
        [ReviewReasons]  = CASE WHEN k.[Keep] = 1 THEN JSON_MODIFY(@ReasonsJson, 'append $', @Marker) ELSE @ReasonsJson END
    FROM [dbo].[TimeEntry] te
    CROSS APPLY (
        SELECT
            CASE WHEN ISJSON(te.[ReviewReasons]) = 1
                  AND EXISTS (SELECT 1 FROM OPENJSON(te.[ReviewReasons]) WHERE [value] = @Marker) THEN 1 ELSE 0 END AS [Had],
            CASE WHEN ISJSON(@ReasonsJson) = 1
                  AND EXISTS (SELECT 1 FROM OPENJSON(@ReasonsJson) WHERE [value] = @Marker) THEN 1 ELSE 0 END AS [Incoming]
    ) h
    CROSS APPLY (
        -- Keep: carry the marker forward when the restamp lacks it. Marked: the
        -- day is a reopened day either way, so 'clean' never lands on it — not
        -- from the sweep, not from the specialist, not from the worker's own
        -- review-flag call that names the marker to get Keep = 0.
        SELECT CASE WHEN h.[Had] = 1 AND h.[Incoming] = 0 AND ISJSON(@ReasonsJson) = 1 THEN 1 ELSE 0 END AS [Keep],
               CASE WHEN h.[Had] = 1 OR h.[Incoming] = 1 THEN 1 ELSE 0 END AS [Marked]
    ) k
    WHERE te.[PublicId] = @TimeEntryPublicId;
    DECLARE @Affected INT = @@ROWCOUNT;
    -- Return what was PERSISTED, not what was asked: on a reopened day the floor
    -- and the carried-forward marker make the two differ.
    IF @ReturnRow = 1
        SELECT @Affected AS [AffectedRowCount], te.[ReviewPriority], te.[ReviewReasons]
        FROM [dbo].[TimeEntry] te WHERE te.[PublicId] = @TimeEntryPublicId;
END;
GO

-- =============================================================================
-- U-596 (2026-10-02): reopen a submitted day on the owner's late log write
-- =============================================================================
-- Compare-and-insert for a status transition. The caller read the entry's
-- current status row (@ExpectedCurrentStatusId) and decided a transition; this
-- inserts ONLY if no newer status row exists for the entry, under a range lock
-- on the entry's status rows, so a concurrent transition (an approval) either
-- committed before us — and we insert nothing — or waits for us. Returns the
-- inserted row, or no row when the status moved underneath the caller.
CREATE OR ALTER PROCEDURE dbo.CreateTimeEntryStatusIfCurrent
(
    @TimeEntryId BIGINT,
    @ExpectedCurrentStatusId BIGINT,
    @Status NVARCHAR(20),
    @UserId BIGINT,
    @Note NVARCHAR(MAX) NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    IF NOT EXISTS (
        SELECT 1 FROM dbo.[TimeEntryStatus] WITH (UPDLOCK, HOLDLOCK)
        WHERE [TimeEntryId] = @TimeEntryId AND [Id] > @ExpectedCurrentStatusId
    )
    BEGIN
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
        VALUES (SYSUTCDATETIME(), @TimeEntryId, @Status, @UserId, @Note);
    END
    ELSE
    BEGIN
        -- The status moved underneath the caller. Return an EMPTY result set
        -- with the same shape — a statement that produces no result set makes
        -- the driver raise instead of returning no row.
        SELECT [Id], [PublicId], [RowVersion],
               CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
               [TimeEntryId], [Status], [UserId], [Note]
        FROM dbo.[TimeEntryStatus] WHERE 1 = 0;
    END
    COMMIT TRANSACTION;
END;
GO

-- 1 when every labor row aggregated from this entry is still a pure derivation
-- of its logs: status 'pending_review' (nobody has reviewed, coded or readied
-- it) and no bill/invoice line points at the parent or any of its line items
-- (a multi-project row stays 'ready' while one project is billed, which
-- IsTimeEntryDownstreamLocked does not see). No labor rows at all is untouched.
-- Only such a day may be reopened without a human: the resubmit then recomputes
-- exactly what submit computed.
CREATE OR ALTER PROCEDURE dbo.IsTimeEntryLaborUntouched
(
    @TimeEntryId BIGINT,
    @Untouched BIT = NULL OUTPUT,     -- for callers inside T-SQL (the aggregator)
    @ReturnRow BIT = 1                -- 0 from inside T-SQL: a result set here would be read
                                      -- by the CALLER's client as the caller's first result set
)
AS
BEGIN
    SET NOCOUNT ON;
    -- Every read below takes UPDLOCK, HOLDLOCK: inside a caller's transaction
    -- (the submit's aggregation, a log write's reopen) the labor rows, their
    -- lines and the Review range stay locked until that transaction commits,
    -- so a reviewer's decision, a mark-ready or a PM's edit cannot land between
    -- this answer and the write that relies on it. Standalone (autocommit) the
    -- locks end with the statement.
    -- EditedSinceAggregation is the durable record that a writer OTHER than the
    -- aggregator touched the row (the generic update sprocs set it): a PM's
    -- in-place correction of an existing line is 'touched' even though it
    -- keeps 'pending_review', its SourceTimeEntryId and creates no Review row.
    DECLARE @Touched BIT = 0;
    -- The office deleted this entry's labor outright (the parent cascade erased
    -- every row-level marker): recorded on the entry, read first.
    IF EXISTS (SELECT 1 FROM dbo.[TimeEntry] te WITH (UPDLOCK, HOLDLOCK)
               WHERE te.[Id] = @TimeEntryId AND te.[LaborDeletedDatetime] IS NOT NULL)
        SET @Touched = 1;
    IF @Touched = 0 AND EXISTS (
        SELECT 1 FROM dbo.[ContractLabor] cl WITH (UPDLOCK, HOLDLOCK)
        WHERE cl.[SourceTimeEntryId] = @TimeEntryId
          AND (cl.[Status] <> 'pending_review'
               OR cl.[EditedSinceAggregation] = 1
               OR cl.[BillLineItemId] IS NOT NULL
               OR EXISTS (SELECT 1 FROM dbo.[Review] r WITH (UPDLOCK, HOLDLOCK) WHERE r.[ContractLaborId] = cl.[Id])
               OR EXISTS (SELECT 1 FROM dbo.[ContractLaborLineItem] li WITH (UPDLOCK, HOLDLOCK)
                          WHERE li.[ContractLaborId] = cl.[Id]
                            AND (li.[SourceTimeEntryId] IS NULL OR li.[EditedSinceAggregation] = 1 OR li.[BillLineItemId] IS NOT NULL)))
    )
        SET @Touched = 1;
    IF @Touched = 0 AND OBJECT_ID('dbo.[EmployeeLabor]', 'U') IS NOT NULL
    BEGIN
        IF EXISTS (
            SELECT 1 FROM dbo.[EmployeeLabor] el WITH (UPDLOCK, HOLDLOCK)
            WHERE el.[SourceTimeEntryId] = @TimeEntryId
              AND (el.[Status] <> 'pending_review'
                   OR el.[EditedSinceAggregation] = 1
                   OR el.[InvoiceLineItemId] IS NOT NULL
                   OR EXISTS (SELECT 1 FROM dbo.[EmployeeLaborLineItem] li WITH (UPDLOCK, HOLDLOCK)
                              WHERE li.[EmployeeLaborId] = el.[Id]
                                AND (li.[SourceTimeEntryId] IS NULL OR li.[EditedSinceAggregation] = 1 OR li.[InvoiceLineItemId] IS NOT NULL)))
        )
            SET @Touched = 1;
    END
    SET @Untouched = CASE WHEN @Touched = 1 THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END;
    IF @ReturnRow = 1 SELECT @Untouched AS Untouched;
END;
GO

-- Submit = the status row AND the aggregation, in one transaction (U-596). Before,
-- they were two commits: a log write could land between them (or between a
-- read and either of them), leaving payroll totals that no longer matched the
-- logs of a 'submitted' day. The status row is a compare-and-insert under the
-- range lock every log write and transition takes, so for the whole of this
-- transaction no log can change; the aggregator's REFUSED (labor someone has
-- reviewed, split, billed or invoiced would change) rolls the status row back
-- with it and re-raises; any OTHER aggregation failure keeps the day
-- submitted, unaggregated, and is reported in the outcome row (the long-
-- standing best-effort semantics the sweep counts as submitted_unaggregated).
-- Result sets, in order: the aggregator's rows if it produced any, then the
-- outcome row (status columns + AggregationError) — always last.
CREATE OR ALTER PROCEDURE dbo.SubmitTimeEntry
(
    @TimeEntryId BIGINT,
    @ExpectedCurrentStatusId BIGINT,
    @UserId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT OFF;
    DECLARE @Outcome TABLE (
        [Id] BIGINT, [PublicId] UNIQUEIDENTIFIER, [RowVersion] BINARY(8), [CreatedDatetime] VARCHAR(19),
        [TimeEntryId] BIGINT, [Status] NVARCHAR(20), [UserId] BIGINT, [Note] NVARCHAR(MAX)
    );
    DECLARE @AggregationError NVARCHAR(4000) = NULL;
    BEGIN TRANSACTION;
    -- Nothing is written before the decision. The status range lock is taken by
    -- this read and HELD to commit; the aggregation runs under a savepoint; the
    -- status row is inserted LAST. A refusal rolls back to the savepoint and
    -- COMMITS (nothing written) before raising — never a full ROLLBACK, which
    -- under pyodbc's autocommit-off zeroes the outer transaction and surfaces
    -- error 266 instead of the refusal. Only a doomed transaction still needs
    -- the full rollback.
    IF EXISTS (
        SELECT 1 FROM dbo.[TimeEntryStatus] WITH (UPDLOCK, HOLDLOCK)
        WHERE [TimeEntryId] = @TimeEntryId AND [Id] > @ExpectedCurrentStatusId
    )
    BEGIN
        -- the status moved underneath the caller: nothing written, empty outcome
        COMMIT TRANSACTION;
        SELECT [Id], [PublicId], [RowVersion], [CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note],
               CAST(NULL AS NVARCHAR(4000)) AS [AggregationError]
        FROM @Outcome;
        RETURN;
    END
    -- Best effort applies to a FIRST aggregation only. If labor already exists for
    -- this entry, a failed rebuild must not leave the OLD labor standing under a
    -- new submission (the logs changed; the labor did not): refuse the submit.
    DECLARE @HadLabor BIT = CASE WHEN EXISTS (SELECT 1 FROM dbo.[ContractLabor] WHERE [SourceTimeEntryId] = @TimeEntryId)
                                   OR (OBJECT_ID('dbo.[EmployeeLabor]', 'U') IS NOT NULL
                                       AND EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [SourceTimeEntryId] = @TimeEntryId))
                                 THEN 1 ELSE 0 END;
    SAVE TRANSACTION AggregationSavepoint;
    BEGIN TRY
        EXEC dbo.AggregateTimeEntryOnSubmit @TimeEntryId = @TimeEntryId;
    END TRY
    BEGIN CATCH
        SET @AggregationError = ERROR_MESSAGE();
        IF XACT_STATE() = -1
        BEGIN
            ROLLBACK TRANSACTION;    -- doomed: the only option; the caller sees a database fault
            RAISERROR(@AggregationError, 16, 1);
            RETURN;
        END
        IF @AggregationError LIKE N'REFUSED:%' OR @HadLabor = 1
        BEGIN
            IF @AggregationError NOT LIKE N'REFUSED:%'
                SET @AggregationError = N'REFUSED: TimeEntry ' + CAST(@TimeEntryId AS NVARCHAR(20))
                    + N' — its labor could not be rebuilt from the logs (' + LEFT(@AggregationError, 200) + N'); the previous labor is left unchanged and the day stays in draft.';
            ROLLBACK TRANSACTION AggregationSavepoint;
            COMMIT TRANSACTION;      -- commits nothing: the savepoint undid the aggregation, no status row was written
            RAISERROR(@AggregationError, 16, 1);
            RETURN;
        END
        ROLLBACK TRANSACTION AggregationSavepoint;   -- first aggregation, best effort: submit unaggregated
    END CATCH
    INSERT INTO dbo.[TimeEntryStatus] ([CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note])
    OUTPUT INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
           CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120),
           INSERTED.[TimeEntryId], INSERTED.[Status], INSERTED.[UserId], INSERTED.[Note]
    INTO @Outcome
    VALUES (SYSUTCDATETIME(), @TimeEntryId, N'submitted', @UserId, NULL);
    COMMIT TRANSACTION;
    SELECT [Id], [PublicId], [RowVersion], [CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note],
           @AggregationError AS [AggregationError]
    FROM @Outcome;
END;
GO
