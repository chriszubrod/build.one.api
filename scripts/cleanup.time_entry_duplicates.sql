-- =============================================================================
-- cleanup.time_entry_duplicates.sql  (2026-06-10)
--
-- Merge/dedup the duplicate TimeLog + TimeEntry rows created by the
-- unprotected iOS retry window (round-2 review; see TODO.md). Audit of
-- 2026-06-10 found ~15 duplicate (TimeEntryId, ClockIn) log groups and
-- 11 duplicate (UserId, WorkDate) entry groups spanning 2026-05-19 →
-- 2026-06-10. The duplicate rows frequently carry the worker's REAL
-- clock-out + note while the app displayed the stranded open twin —
-- this data is recovered, not deleted.
--
-- SAFETY MODEL
--   @Apply = 0 (default): report-only. Prints exactly what would happen.
--   @Apply = 1: performs the merge inside a transaction.
--
-- RULES
--   TimeLog groups (same TimeEntryId + ClockIn):
--     - If >1 row is CLOSED and their ClockOuts differ -> SKIP (flagged
--       for office review; conflicting evidence, human call).
--     - Else keep the best row: closed beats open; longer note beats
--       shorter; lowest Id breaks ties. Delete the others.
--   TimeEntry groups (same UserId + WorkDate):
--     - If ANY entry in the group is not in 'draft' status -> SKIP
--       (flagged; submitted/approved days need office review).
--     - Keeper = most TimeLogs, then lowest Id. Losers' TimeLogs are
--       RE-POINTED to the keeper (never deleted). Keeper inherits a
--       loser's note if the keeper's is empty. Losers' TimeEntryStatus
--       rows are deleted, then the losers.
--
-- Run AFTER this: scripts/migrations/time_log_update_guards_and_unique_indexes.sql
-- (creates the unique indexes that prevent recurrence — they require a
-- clean table).
--
-- Run with: python scripts/run_sql.py scripts/cleanup.time_entry_duplicates.sql
-- =============================================================================

DECLARE @Apply BIT = 0;   -- <<< flip to 1 to perform the merge

SET NOCOUNT ON;
BEGIN TRANSACTION;

-- -----------------------------------------------------------------------------
-- 1. TimeLog duplicates
-- -----------------------------------------------------------------------------
DECLARE @LogGroups TABLE (TimeEntryId BIGINT, ClockIn DATETIME2(3),
                          ClosedCount INT, DistinctClosedOuts INT);
INSERT INTO @LogGroups
SELECT [TimeEntryId], [ClockIn],
       SUM(CASE WHEN [ClockOut] IS NOT NULL THEN 1 ELSE 0 END),
       COUNT(DISTINCT [ClockOut])
FROM dbo.[TimeLog]
GROUP BY [TimeEntryId], [ClockIn]
HAVING COUNT(*) > 1;

PRINT '=== TimeLog duplicate groups: CONFLICTING (skipped — office review) ===';
SELECT tl.[TimeEntryId], tl.[Id] AS LogId,
       CONVERT(VARCHAR(23), tl.[ClockIn], 121) AS ClockIn,
       CONVERT(VARCHAR(23), tl.[ClockOut], 121) AS ClockOut,
       LEFT(COALESCE(tl.[Note], ''), 60) AS Note
FROM dbo.[TimeLog] tl
JOIN @LogGroups g ON g.TimeEntryId = tl.[TimeEntryId] AND g.ClockIn = tl.[ClockIn]
WHERE g.ClosedCount > 1 AND g.DistinctClosedOuts > 1
ORDER BY tl.[TimeEntryId], tl.[Id];

-- Rank survivors for the mergeable groups
DECLARE @LogLosers TABLE (LogId BIGINT);
INSERT INTO @LogLosers
SELECT tl.[Id]
FROM dbo.[TimeLog] tl
JOIN @LogGroups g ON g.TimeEntryId = tl.[TimeEntryId] AND g.ClockIn = tl.[ClockIn]
WHERE NOT (g.ClosedCount > 1 AND g.DistinctClosedOuts > 1)   -- conflicting groups skipped
  AND tl.[Id] <> (
      SELECT TOP 1 best.[Id]
      FROM dbo.[TimeLog] best
      WHERE best.[TimeEntryId] = tl.[TimeEntryId] AND best.[ClockIn] = tl.[ClockIn]
      ORDER BY CASE WHEN best.[ClockOut] IS NOT NULL THEN 0 ELSE 1 END,
               LEN(COALESCE(best.[Note], '')) DESC,
               best.[Id] ASC
  );

PRINT '=== TimeLog rows to DELETE (open/strictly-inferior twins) ===';
SELECT tl.[TimeEntryId], tl.[Id] AS LogId,
       CONVERT(VARCHAR(23), tl.[ClockIn], 121) AS ClockIn,
       CONVERT(VARCHAR(23), tl.[ClockOut], 121) AS ClockOut,
       LEFT(COALESCE(tl.[Note], ''), 60) AS Note
FROM dbo.[TimeLog] tl JOIN @LogLosers l ON l.LogId = tl.[Id]
ORDER BY tl.[TimeEntryId], tl.[Id];

IF @Apply = 1
BEGIN
    DELETE tl FROM dbo.[TimeLog] tl JOIN @LogLosers l ON l.LogId = tl.[Id];
    PRINT CONCAT('Deleted ', @@ROWCOUNT, ' duplicate TimeLog rows.');
END

-- -----------------------------------------------------------------------------
-- 2. TimeEntry duplicates
-- -----------------------------------------------------------------------------
-- Latest status per entry (highest Id wins = most recent transition)
DECLARE @EntryStatus TABLE (TimeEntryId BIGINT PRIMARY KEY, LatestStatus NVARCHAR(20));
INSERT INTO @EntryStatus
SELECT s.[TimeEntryId], s.[Status]
FROM dbo.[TimeEntryStatus] s
WHERE s.[Id] = (SELECT MAX(x.[Id]) FROM dbo.[TimeEntryStatus] x WHERE x.[TimeEntryId] = s.[TimeEntryId]);

DECLARE @EntryGroups TABLE (UserId BIGINT, WorkDate DATE, NonDraft INT);
INSERT INTO @EntryGroups
SELECT te.[UserId], te.[WorkDate],
       SUM(CASE WHEN COALESCE(es.LatestStatus, 'draft') <> 'draft' THEN 1 ELSE 0 END)
FROM dbo.[TimeEntry] te
LEFT JOIN @EntryStatus es ON es.TimeEntryId = te.[Id]
GROUP BY te.[UserId], te.[WorkDate]
HAVING COUNT(*) > 1;

PRINT '=== TimeEntry duplicate groups: NON-DRAFT (skipped — office review) ===';
SELECT te.[UserId], CONVERT(VARCHAR(10), te.[WorkDate], 120) AS WorkDate, te.[Id] AS EntryId,
       COALESCE(es.LatestStatus, 'draft') AS LatestStatus
FROM dbo.[TimeEntry] te
JOIN @EntryGroups g ON g.UserId = te.[UserId] AND g.WorkDate = te.[WorkDate]
LEFT JOIN @EntryStatus es ON es.TimeEntryId = te.[Id]
WHERE g.NonDraft > 0
ORDER BY te.[UserId], te.[WorkDate], te.[Id];

-- Keepers for the mergeable (all-draft) groups
DECLARE @Keepers TABLE (UserId BIGINT, WorkDate DATE, KeeperId BIGINT);
INSERT INTO @Keepers
SELECT g.UserId, g.WorkDate,
       (SELECT TOP 1 te.[Id]
        FROM dbo.[TimeEntry] te
        WHERE te.[UserId] = g.UserId AND te.[WorkDate] = g.WorkDate
        ORDER BY (SELECT COUNT(*) FROM dbo.[TimeLog] tl WHERE tl.[TimeEntryId] = te.[Id]) DESC,
                 te.[Id] ASC)
FROM @EntryGroups g
WHERE g.NonDraft = 0;

DECLARE @EntryLosers TABLE (LoserId BIGINT, KeeperId BIGINT);
INSERT INTO @EntryLosers
SELECT te.[Id], k.KeeperId
FROM dbo.[TimeEntry] te
JOIN @Keepers k ON k.UserId = te.[UserId] AND k.WorkDate = te.[WorkDate]
WHERE te.[Id] <> k.KeeperId;

PRINT '=== TimeEntry merge plan (loser -> keeper; loser logs re-pointed) ===';
SELECT el.LoserId, el.KeeperId,
       (SELECT COUNT(*) FROM dbo.[TimeLog] tl WHERE tl.[TimeEntryId] = el.LoserId) AS LoserLogCount,
       LEFT(COALESCE((SELECT te.[Note] FROM dbo.[TimeEntry] te WHERE te.[Id] = el.LoserId), ''), 40) AS LoserNote
FROM @EntryLosers el
ORDER BY el.KeeperId;

IF @Apply = 1
BEGIN
    -- Re-point losers' logs at the keeper (recovers hidden hours)
    UPDATE tl SET tl.[TimeEntryId] = el.KeeperId
    FROM dbo.[TimeLog] tl JOIN @EntryLosers el ON el.LoserId = tl.[TimeEntryId];
    PRINT CONCAT('Re-pointed ', @@ROWCOUNT, ' TimeLog rows to keeper entries.');

    -- Keeper inherits a loser's note when the keeper's is empty
    UPDATE keeper
    SET keeper.[Note] = loser.[Note]
    FROM dbo.[TimeEntry] keeper
    JOIN @EntryLosers el ON el.KeeperId = keeper.[Id]
    JOIN dbo.[TimeEntry] loser ON loser.[Id] = el.LoserId
    WHERE COALESCE(keeper.[Note], '') = '' AND COALESCE(loser.[Note], '') <> '';

    -- Drop losers' status audit rows, then the losers
    DELETE s FROM dbo.[TimeEntryStatus] s JOIN @EntryLosers el ON el.LoserId = s.[TimeEntryId];
    DELETE te FROM dbo.[TimeEntry] te JOIN @EntryLosers el ON el.LoserId = te.[Id];
    PRINT CONCAT('Deleted ', @@ROWCOUNT, ' duplicate TimeEntry rows.');
END

IF @Apply = 1
BEGIN
    COMMIT TRANSACTION;
    PRINT 'APPLIED.';
END
ELSE
BEGIN
    ROLLBACK TRANSACTION;
    PRINT 'DRY RUN ONLY — nothing changed. Flip @Apply to 1 to perform the merge.';
END
