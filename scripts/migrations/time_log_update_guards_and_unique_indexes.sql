-- =============================================================================
-- time_log_update_guards_and_unique_indexes.sql  (2026-06-10)
--
-- Round-2 review remediation (see build.one.api/TODO.md "Time-tracking
-- round-2 review"):
--
-- 1. NULL-overwrite guards on UpdateTimeLogById / UpdateTimeEntryById.
--    The 2026-05-26 time_entry_view_team.sql migration copied the base
--    sprocs' unconditional SET pattern. Guards are added ONLY for fields
--    that can never be legitimately nulled by an update:
--      - TimeLog.ClockIn / LogType (NOT NULL by schema)
--      - TimeLog.Latitude / Longitude (GPS evidence is append-only by
--        product intent — a partial update must never erase a recorded fix)
--      - TimeEntry.UserId / WorkDate (NOT NULL by schema)
--      - TimeEntry.Note (iOS sends non-null always; clearing uses '')
--    ClockOut / Duration / ProjectId / TimeLog.Note stay unconditional:
--    NULL is a legitimate target value there (reopening a log clears
--    ClockOut; clearing a note sends NULL).
--
-- 2. Unique indexes the iOS duplicate-claim recovery assumes exist:
--      UX_TimeLog_TimeEntry_ClockIn   ON dbo.TimeLog(TimeEntryId, ClockIn)
--      UX_TimeEntry_UserId_WorkDate   ON dbo.TimeEntry(UserId, WorkDate)
--    Each creation is GATED on a duplicate audit: if duplicates already
--    exist in prod (created during the unprotected retry window), the
--    script prints the offending groups and SKIPS the index. Dedup those
--    rows manually, then re-run this script (idempotent).
--
-- Run with: python scripts/run_sql.py scripts/migrations/time_log_update_guards_and_unique_indexes.sql
-- =============================================================================

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

-- =============================================================================
-- Unique index: dbo.TimeLog (TimeEntryId, ClockIn)
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM dbo.[TimeLog]
    GROUP BY [TimeEntryId], [ClockIn]
    HAVING COUNT(*) > 1
)
BEGIN
    PRINT 'WARNING: duplicate (TimeEntryId, ClockIn) groups exist — UX_TimeLog_TimeEntry_ClockIn NOT created. Dedup these rows, then re-run:';
    SELECT [TimeEntryId], CONVERT(VARCHAR(23), [ClockIn], 121) AS [ClockIn], COUNT(*) AS [DupCount]
    FROM dbo.[TimeLog]
    GROUP BY [TimeEntryId], [ClockIn]
    HAVING COUNT(*) > 1;
END
ELSE IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE [name] = 'UX_TimeLog_TimeEntry_ClockIn'
      AND [object_id] = OBJECT_ID('dbo.TimeLog')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UX_TimeLog_TimeEntry_ClockIn]
        ON dbo.[TimeLog]([TimeEntryId], [ClockIn]);
    PRINT 'Created UX_TimeLog_TimeEntry_ClockIn.';
END
ELSE
    PRINT 'UX_TimeLog_TimeEntry_ClockIn already exists — skipped.';
GO

-- =============================================================================
-- Unique index: dbo.TimeEntry (UserId, WorkDate)
-- One entry per user per day is the data model both clients assume
-- (iOS todayEntry / fetchByUserAndDate, the duplicate-claim recovery,
-- and the web week views).
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM dbo.[TimeEntry]
    GROUP BY [UserId], [WorkDate]
    HAVING COUNT(*) > 1
)
BEGIN
    PRINT 'WARNING: duplicate (UserId, WorkDate) groups exist — UX_TimeEntry_UserId_WorkDate NOT created. Dedup these rows, then re-run:';
    SELECT [UserId], CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate], COUNT(*) AS [DupCount]
    FROM dbo.[TimeEntry]
    GROUP BY [UserId], [WorkDate]
    HAVING COUNT(*) > 1;
END
ELSE IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE [name] = 'UX_TimeEntry_UserId_WorkDate'
      AND [object_id] = OBJECT_ID('dbo.TimeEntry')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UX_TimeEntry_UserId_WorkDate]
        ON dbo.[TimeEntry]([UserId], [WorkDate]);
    PRINT 'Created UX_TimeEntry_UserId_WorkDate.';
END
ELSE
    PRINT 'UX_TimeEntry_UserId_WorkDate already exists — skipped.';
GO
