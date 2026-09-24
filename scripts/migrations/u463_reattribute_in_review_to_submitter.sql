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
-- PROD OUTCOME (2026-09-15, audited after apply). 21 rows re-attributed; ZERO
-- misattributions found. 13 verified against `[ms].[Outbox]` `Kind='send_mail'`
-- audit trail (`Payload.review_id` / `bill_id` linkage); 8 unambiguous because
-- their parent bill has exactly one `submitted` row. `CreatedByUserId` stayed
-- 33 (system actor) on all 21. There is no remaining population to repair.
--
-- SCOPE. Bill pipeline rows only (`BillId IS NOT NULL`). Only rows that are ALL of:
--   * ReviewKind = 'in_review'            (frozen at insert — see dbo.review.sql)
--   * UserId     = the system actor       (typical for pipeline auto-advance)
--   * exactly ONE `submitted` row on the parent bill (see guard below)
--   * a resolvable submitter for that lone `submitted` row
-- Everything else is left alone. In particular this does NOT touch the 268
-- other system-actor review rows — those are genuine agent work.
--
-- DO NOT reuse this script for Invoice. There is no bill-style notification
-- pipeline for Invoice (only `_advance_to_in_review` on Bill and
-- `_advance_expense_to_in_review` on Expense exist). The ~18 system-actor
-- `in_review` Invoice rows were never written by this path; re-attributing
-- them would be wrong.
--
-- INFERENCE vs FACT (read before copying this file). The submitter join below
-- is TIMESTAMP PROXIMITY INFERENCE, not ground truth. The FACT is recorded in
-- the same synchronous `ReviewNotificationService._do_enqueue` call: it enqueues
-- `[ms].[Outbox]` `Kind='send_mail'` with `Payload.review_id` (and `bill_id`
-- from ~2026-08-11 onward) for the `submitted` row, then calls
-- `_advance_to_in_review`, which inserts the `in_review` row ~0.5–3.1 seconds
-- later in the same HTTP request — not "after a resubmit cycle". Normal
-- submit → decline → resubmit over minutes or days resolves correctly; the
-- inference breaks only if a second `submitted` row on the same bill lands
-- BETWEEN those two inserts in one request (vanishingly rare). Do NOT copy this
-- inference into a new backfill without also requiring the outbox linkage
-- (when payload fields exist).
--
-- GAP-2 HAZARD (documented; no hard outbox gate in this spent script). The
-- candidate predicate `ReviewKind='in_review' AND UserId=@SystemActorId` also
-- matches a genuine agent review action on an intermediate status: same
-- `UserId=33`, same kind, and `CreatedByUserId=33` on both. There is no column
-- on `dbo.Review` alone that separates them. The outbox `send_mail` row for
-- the parent bill proves the pipeline wrote the row, but `review_id`/`bill_id`
-- in JSON were only populated from ~2026-08-11 onward — older outbox rows carry
-- nulls, so a hard NOT EXISTS outbox requirement would skip legitimate pipeline
-- rows. This file therefore documents the hazard in the header rather than
-- pretending a partial outbox filter is safe. New backfills must join outbox
-- when payloads allow it, and treat unmatched system-actor `in_review` rows as
-- manual review.
--
-- `CreatedByUserId` IS DELIBERATELY UNTOUCHED. It already records the pipeline,
-- and that is the fact this migration exists to preserve: after it runs,
-- "did a human move this or did the system?" is still answerable in SQL.
--
-- GUARDS (for safe re-run and safe copy-as-template).
--   * Parent has more than one `submitted` row → SKIP and REPORT (never UPDATE).
--     Only 1 of 408 bills ever had a resubmit cycle; none of the 21 prod rows
--     needed this guard, but it blocks ambiguous inference on copy.
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

-- Parent bills with more than one submitted row: timestamp inference is unsafe.
IF OBJECT_ID('tempdb..#u463_multi_submit') IS NOT NULL DROP TABLE #u463_multi_submit;
SELECT s.[BillId]
INTO #u463_multi_submit
FROM dbo.[Review] s
WHERE s.[ReviewKind] = N'submitted'
  AND s.[BillId] IS NOT NULL
GROUP BY s.[BillId]
HAVING COUNT(*) > 1;

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
    SUM(CASE WHEN EXISTS (
              SELECT 1 FROM #u463_multi_submit m WHERE m.[BillId] = t.[BillId]
          ) THEN 1 ELSE 0 END)                                     AS [multi_submit_skipped],
    SUM(CASE WHEN [SubmitterUserId] IS NULL THEN 1 ELSE 0 END)     AS [no_submitter_skipped],
    SUM(CASE WHEN [SubmitterUserId] = [CurrentUserId] THEN 1 ELSE 0 END) AS [already_correct],
    SUM(CASE WHEN [SubmitterUserId] IS NOT NULL
              AND [SubmitterUserId] <> [CurrentUserId]
              AND NOT EXISTS (
                  SELECT 1 FROM #u463_multi_submit m WHERE m.[BillId] = t.[BillId]
              ) THEN 1 ELSE 0 END)                                 AS [would_update]
FROM #u463 t;

PRINT '--- U-463 multi-submit parents (inference skipped) ---';
SELECT
    t.[ReviewId],
    t.[BillId],
    t.[CreatedDatetime],
    (SELECT COUNT(*)
     FROM dbo.[Review] s
     WHERE s.[BillId] = t.[BillId] AND s.[ReviewKind] = N'submitted') AS [submitted_row_count]
FROM #u463 t
WHERE EXISTS (SELECT 1 FROM #u463_multi_submit m WHERE m.[BillId] = t.[BillId])
ORDER BY t.[BillId], t.[CreatedDatetime];

-- Who the rows would move to, so the change is inspectable before it is made.
SELECT
    ISNULL(a.[Username], CONCAT('user#', t.[SubmitterUserId])) AS [new_actor],
    COUNT(*) AS [rows]
FROM #u463 t
LEFT JOIN dbo.[User] u ON u.[Id] = t.[SubmitterUserId]
LEFT JOIN dbo.[Auth] a ON a.[UserId] = u.[Id]
WHERE t.[SubmitterUserId] IS NOT NULL AND t.[SubmitterUserId] <> t.[CurrentUserId]
  AND NOT EXISTS (SELECT 1 FROM #u463_multi_submit m WHERE m.[BillId] = t.[BillId])
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
      AND NOT EXISTS (
          SELECT 1 FROM #u463_multi_submit m WHERE m.[BillId] = t.[BillId]
      )
      -- Re-assert the guard at write time: #u463 was built earlier in this
      -- transaction, and a concurrent write could have moved the row since.
      AND r.[UserId] = @SystemActorId
      AND r.[ReviewKind] = N'in_review';

    PRINT CONCAT('U-463: re-attributed ', @@ROWCOUNT, ' In Review row(s) to their submitter.');
END
ELSE
    PRINT 'U-463: PREVIEW ONLY — set @Apply = 1 to write.';

DROP TABLE #u463;
DROP TABLE #u463_multi_submit;
