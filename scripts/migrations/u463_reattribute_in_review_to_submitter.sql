-- U-463 — re-attribute the auto-advance "In Review" rows to their submitter.
--
-- WHAT HAPPENED. When a person submits a bill for review, the notification
-- pipeline emails the reviewers and then advances the review Submitted ->
-- In Review. LS-01c′/U-453 attributed that advance to the system actor
-- (`claude_agent`), reasoning that the machine moved the state. True about the
-- row, wrong about the experience: `ReviewTimeline` renders the LATEST row's
-- actor as the headline, so every bill a person submitted read
-- "In Review · by Claude Agent". Reported by Chris on 2026-09-15.
--
-- U-463 splits the two facts going forward: `UserId` (the ACTOR the timeline
-- renders) is the submitter; `CreatedByUserId` (the AUDIT SUBJECT) stays the
-- pipeline. This script applies the same split to the rows already written.
--
-- SCOPE. Only rows that are ALL of:
--   * ReviewKind = 'in_review'            (the auto-advance, not a human step)
--   * UserId     = the system actor       (written by the pipeline)
--   * have a resolvable submitter          (see the cycle rule below)
-- Everything else is left alone. In particular this does NOT touch the 268
-- other system-actor review rows — those are genuine agent work.
--
-- THE CYCLE RULE. A review can cycle: submit -> decline -> submit again. The
-- submitter of a given In Review row is the author of the latest 'submitted'
-- row on the SAME parent at or before it — not simply the parent's first
-- submission, which would credit the wrong person after a resubmit.
--
-- `CreatedByUserId` IS DELIBERATELY UNTOUCHED. It already records the pipeline,
-- and that is the fact this migration exists to preserve: after it runs,
-- "did a human move this or did the system?" is still answerable in SQL.
--
-- IDEMPOTENT. Re-running changes nothing once applied — the WHERE clause stops
-- matching as soon as UserId is no longer the system actor.
--
-- USAGE (from the repo root, dry run by DEFAULT):
--   ./.venv/bin/python scripts/run_sql.py scripts/migrations/u463_reattribute_in_review_to_submitter.sql
-- Set @Apply = 1 below to actually write. `run_sql.py` runs the whole file in
-- ONE transaction and rolls back on any error, so a failed apply leaves nothing
-- half-done.

SET NOCOUNT ON;

DECLARE @Apply BIT = 0;   -- <<< 0 = preview only. Set to 1 to write.

DECLARE @SystemActorId BIGINT = (
    SELECT TOP 1 u.[Id]
    FROM dbo.[User] u
    INNER JOIN dbo.[Auth] a ON a.[UserId] = u.[Id]
    WHERE a.[Username] = 'claude_agent'
);

IF @SystemActorId IS NULL
BEGIN
    -- Resolved by username, never hard-coded: id 33 is correct in prod and
    -- potentially a HUMAN in any other database (U-453, Codex P1).
    RAISERROR('U-463: could not resolve the system actor by username ''claude_agent''; refusing to guess an id.', 16, 1);
    RETURN;
END

-- Candidate rows plus the submitter that owns each one's cycle.
IF OBJECT_ID('tempdb..#u463') IS NOT NULL DROP TABLE #u463;
SELECT
    r.[Id]                AS [ReviewId],
    r.[BillId],
    r.[CreatedDatetime],
    r.[UserId]            AS [CurrentUserId],
    s.[UserId]            AS [SubmitterUserId]
INTO #u463
FROM dbo.[Review] r
OUTER APPLY (
    SELECT TOP 1 s2.[UserId]
    FROM dbo.[Review] s2
    WHERE s2.[BillId] = r.[BillId]
      AND s2.[ReviewKind] = N'submitted'
      AND s2.[CreatedDatetime] <= r.[CreatedDatetime]
    ORDER BY s2.[CreatedDatetime] DESC, s2.[Id] DESC
) s
WHERE r.[ReviewKind] = N'in_review'
  AND r.[UserId] = @SystemActorId
  AND r.[BillId] IS NOT NULL;

PRINT '--- U-463 backfill preview ---';
SELECT
    COUNT(*)                                                       AS [candidates],
    SUM(CASE WHEN [SubmitterUserId] IS NULL THEN 1 ELSE 0 END)     AS [no_submitter_skipped],
    SUM(CASE WHEN [SubmitterUserId] = [CurrentUserId] THEN 1 ELSE 0 END) AS [already_correct],
    SUM(CASE WHEN [SubmitterUserId] IS NOT NULL
              AND [SubmitterUserId] <> [CurrentUserId] THEN 1 ELSE 0 END) AS [would_update]
FROM #u463;

-- Who the rows would move to, so the change is inspectable before it is made.
SELECT
    ISNULL(a.[Username], CONCAT('user#', t.[SubmitterUserId])) AS [new_actor],
    COUNT(*) AS [rows]
FROM #u463 t
LEFT JOIN dbo.[User] u ON u.[Id] = t.[SubmitterUserId]
LEFT JOIN dbo.[Auth] a ON a.[UserId] = u.[Id]
WHERE t.[SubmitterUserId] IS NOT NULL AND t.[SubmitterUserId] <> t.[CurrentUserId]
GROUP BY ISNULL(a.[Username], CONCAT('user#', t.[SubmitterUserId]));

IF @Apply = 1
BEGIN
    UPDATE r
       SET r.[UserId] = t.[SubmitterUserId],
           r.[ModifiedDatetime] = SYSUTCDATETIME()
    FROM dbo.[Review] r
    INNER JOIN #u463 t ON t.[ReviewId] = r.[Id]
    WHERE t.[SubmitterUserId] IS NOT NULL
      AND t.[SubmitterUserId] <> t.[CurrentUserId]
      -- Re-assert the guard at write time: #u463 was built earlier in this
      -- transaction, and a concurrent write could have moved the row since.
      AND r.[UserId] = @SystemActorId
      AND r.[ReviewKind] = N'in_review';

    PRINT CONCAT('U-463: re-attributed ', @@ROWCOUNT, ' In Review row(s) to their submitter.');
END
ELSE
    PRINT 'U-463: PREVIEW ONLY — set @Apply = 1 to write.';

DROP TABLE #u463;
