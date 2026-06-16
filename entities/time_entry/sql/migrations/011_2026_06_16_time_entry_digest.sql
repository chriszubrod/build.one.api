-- =============================================================================
-- 2026-06-16 — Time-Entry daily digest support.
--
-- Powers the morning "here's the time recorded for you yesterday" email each
-- worker receives so they can confirm correctness. Two sprocs:
--
--   1. dbo.ReadTimeEntriesForDigestByWorkDate(@WorkDate)
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
--   2. dbo.CountMsOutboxByEntity(@EntityType, @EntityPublicId)
--        Generic idempotency helper: counts [ms].[Outbox] rows for an
--        (EntityType, EntityPublicId) pair regardless of status. The digest
--        service uses it with a DETERMINISTIC EntityPublicId per (worker,
--        work_date) (a uuid5) + EntityType='TimeEntryDigest' so a re-run of
--        the daily sweep never double-enqueues a worker's digest — including
--        after the first row has already drained to 'done' (which the
--        pending-only ReadPendingMsOutboxByEntity would miss). Introduced here
--        because the digest is its first consumer; it's deliberately generic.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Digest resolver — entries + logs + worker email + project + status
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


-- -----------------------------------------------------------------------------
-- 2. Generic MS-outbox idempotency count (any status)
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.CountMsOutboxByEntity
(
    @EntityType     NVARCHAR(32),
    @EntityPublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT COUNT(*) AS [Cnt]
    FROM [ms].[Outbox]
    WHERE [EntityType] = @EntityType
      AND [EntityPublicId] = @EntityPublicId;
END;
GO


PRINT 'ReadTimeEntriesForDigestByWorkDate created.';
PRINT 'CountMsOutboxByEntity created.';
PRINT 'Migration 011 applied — time-entry daily digest support.';
