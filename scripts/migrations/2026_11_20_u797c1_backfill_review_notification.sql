-- =============================================================================
-- U-797c1 backfill: seed dbo.ReviewNotification / dbo.ReviewNotificationTarget
-- from history, so replies to emails sent BEFORE c1 bind through the record.
--
-- One-shot, set-based, idempotent (every insert is NOT EXISTS-guarded; a re-run
-- inserts nothing). Not a stored procedure: this file defines no routines. Rows
-- written here carry Source = N'backfill'. Run from the repo root:
--   ./.venv/bin/python scripts/run_sql.py scripts/migrations/2026_11_20_u797c1_backfill_review_notification.sql
--
-- Bill / Expense: one row per review_id from the LATEST completed send_mail outbox
--   row carrying that review_id. Other outbox rows for the same review are listed
--   by `scripts/u797c1_rebind_proof.py --report` (the latest one wins).
-- Contract Labor: one row per (ProjectId, WorkDate) ContractLaborNotification claim (NULL WorkDate skipped). ConversationId is the
--   single distinct conversation id from (a) a completed ContractLabor send_mail
--   payload with the same subject, else (b) a sent-items EmailMessage with the same
--   subject (outbound, as the legacy resolver matches); ambiguous or absent -> NULL and listed by --report. Targets = every
--   ContractLabor with a line item on the claim's project and the claim's WorkDate.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Bill / Expense notifications, from completed send_mail outbox rows.
-- -----------------------------------------------------------------------------
;WITH sends AS (
    SELECT o.[Id]                                                AS OutboxId,
           o.[EntityType]                                        AS EntityType,
           TRY_CAST(JSON_VALUE(o.[Payload], '$.review_id') AS BIGINT) AS ReviewId,
           JSON_VALUE(o.[Payload], '$.conversation_id')          AS ConversationId,
           JSON_VALUE(o.[Payload], '$.internet_message_id')      AS InternetMessageId,
           JSON_VALUE(o.[Payload], '$.graph_message_id')         AS DraftGraphMessageId,
           JSON_VALUE(o.[Payload], '$.subject')                  AS Subject,
           o.[CreatedDatetime],
           ROW_NUMBER() OVER (
               PARTITION BY TRY_CAST(JSON_VALUE(o.[Payload], '$.review_id') AS BIGINT)
               ORDER BY o.[CreatedDatetime] DESC, o.[Id] DESC) AS rn
    FROM [ms].[Outbox] o
    WHERE o.[Kind] = N'send_mail'
      AND o.[Status] = N'done'
      AND o.[EntityType] IN (N'Bill', N'Expense')
      AND TRY_CAST(JSON_VALUE(o.[Payload], '$.review_id') AS BIGINT) IS NOT NULL
)
INSERT INTO dbo.[ReviewNotification]
    ([Kind], [ReviewId], [OutboxId], [Subject], [ConversationId], [InternetMessageId],
     [DraftGraphMessageId], [StampedDatetime], [Source])
SELECT CASE WHEN s.[EntityType] = N'Bill' THEN N'bill' ELSE N'expense' END,
       s.[ReviewId], s.[OutboxId], s.[Subject], s.[ConversationId], s.[InternetMessageId],
       s.[DraftGraphMessageId],
       CASE WHEN s.[ConversationId] IS NOT NULL THEN SYSUTCDATETIME() END,
       N'backfill'
FROM sends s
JOIN dbo.[Review] r ON r.[Id] = s.[ReviewId]
WHERE s.rn = 1
  AND (   (s.[EntityType] = N'Bill'    AND r.[BillId]    IS NOT NULL)
       OR (s.[EntityType] = N'Expense' AND r.[ExpenseId] IS NOT NULL))
  AND NOT EXISTS (SELECT 1 FROM dbo.[ReviewNotification] n WHERE n.[ReviewId] = s.[ReviewId]);
GO

INSERT INTO dbo.[ReviewNotificationTarget]
    ([ReviewNotificationId], [BillId], [ExpenseId], [BillCreditId], [ContractLaborId])
SELECT n.[Id],
       CASE WHEN n.[Kind] = N'bill'    THEN r.[BillId]    END,
       CASE WHEN n.[Kind] = N'expense' THEN r.[ExpenseId] END,
       NULL,
       NULL
FROM dbo.[ReviewNotification] n
JOIN dbo.[Review] r ON r.[Id] = n.[ReviewId]
WHERE n.[Source] = N'backfill'
  AND n.[Kind] IN (N'bill', N'expense')
  AND (   (n.[Kind] = N'bill'    AND r.[BillId]    IS NOT NULL)
       OR (n.[Kind] = N'expense' AND r.[ExpenseId] IS NOT NULL))
  AND NOT EXISTS (SELECT 1 FROM dbo.[ReviewNotificationTarget] t WHERE t.[ReviewNotificationId] = n.[Id]);
GO

-- -----------------------------------------------------------------------------
-- 2. Contract Labor notifications, one per ContractLaborNotification claim.
--    Candidate conversation ids are resolved into a temp table first.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('tempdb..#u797c1_cl_claims') IS NOT NULL DROP TABLE #u797c1_cl_claims;
-- Only claims with a WorkDate are backfilled (a NULL date cannot be a (project, date) key).
-- One claim per (ProjectId, WorkDate): the lowest Id wins, deterministically. The others are
-- skipped here and listed by `scripts/u797c1_rebind_proof.py --report`.
SELECT x.[ClaimId], x.[ProjectId], x.[WorkDate], x.[OutboundSubject]
INTO #u797c1_cl_claims
FROM (
    SELECT c.[Id] AS ClaimId, c.[ProjectId], c.[WorkDate], c.[OutboundSubject],
           ROW_NUMBER() OVER (PARTITION BY c.[ProjectId], c.[WorkDate] ORDER BY c.[Id]) AS rn
    FROM dbo.[ContractLaborNotification] c
    WHERE c.[WorkDate] IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM dbo.[ReviewNotification] n
          WHERE n.[Kind] = N'contract_labor'
            AND n.[ProjectId] = c.[ProjectId]
            AND n.[WorkDate] = c.[WorkDate])
) x
WHERE x.rn = 1;

-- (a) completed ContractLabor send_mail payloads carrying a conversation id.
IF OBJECT_ID('tempdb..#u797c1_cl_outbox') IS NOT NULL DROP TABLE #u797c1_cl_outbox;
SELECT DISTINCT k.[ClaimId],
       JSON_VALUE(o.[Payload], '$.conversation_id') AS ConversationId
INTO #u797c1_cl_outbox
FROM #u797c1_cl_claims k
JOIN [ms].[Outbox] o
  ON o.[EntityType] = N'ContractLabor'
 AND o.[Kind] = N'send_mail'
 AND o.[Status] = N'done'
 AND JSON_VALUE(o.[Payload], '$.subject') COLLATE Latin1_General_BIN = k.[OutboundSubject] COLLATE Latin1_General_BIN
WHERE JSON_VALUE(o.[Payload], '$.conversation_id') IS NOT NULL;

-- (b) outbound EmailMessages with the same subject. Same rule as today's legacy resolver
-- (FindContractLaborForReviewerReply: ProcessingStatus = 'outbound', OutboundSubject = Subject), so the
-- proof compares like with like. Keep in sync with REPORT_SQL["cl_candidates"] in the proof script.
IF OBJECT_ID('tempdb..#u797c1_cl_sent') IS NOT NULL DROP TABLE #u797c1_cl_sent;
SELECT DISTINCT k.[ClaimId],
       m.[ConversationId] AS ConversationId
INTO #u797c1_cl_sent
FROM #u797c1_cl_claims k
JOIN dbo.[EmailMessage] m
  ON m.[ProcessingStatus] = N'outbound'
 AND m.[Subject] = k.[OutboundSubject]
WHERE m.[ConversationId] IS NOT NULL;

IF OBJECT_ID('tempdb..#u797c1_cl_resolved') IS NOT NULL DROP TABLE #u797c1_cl_resolved;
SELECT k.[ClaimId],
       k.[ProjectId],
       k.[WorkDate],
       k.[OutboundSubject],
       CASE
           WHEN ob.[Distinct] = 1 THEN ob.[OneConv]
           WHEN ob.[Distinct] = 0 AND sn.[Distinct] = 1 THEN sn.[OneConv]
           ELSE NULL
       END AS ConversationId
INTO #u797c1_cl_resolved
FROM #u797c1_cl_claims k
OUTER APPLY (
    SELECT COUNT(DISTINCT x.[ConversationId]) AS [Distinct],
           MIN(x.[ConversationId])            AS [OneConv]
    FROM #u797c1_cl_outbox x WHERE x.[ClaimId] = k.[ClaimId]) ob
OUTER APPLY (
    SELECT COUNT(DISTINCT y.[ConversationId]) AS [Distinct],
           MIN(y.[ConversationId])            AS [OneConv]
    FROM #u797c1_cl_sent y WHERE y.[ClaimId] = k.[ClaimId]) sn;
GO

INSERT INTO dbo.[ReviewNotification]
    ([Kind], [ProjectId], [WorkDate], [Subject], [ConversationId], [StampedDatetime], [Source])
SELECT N'contract_labor', r.[ProjectId], r.[WorkDate], r.[OutboundSubject], r.[ConversationId],
       CASE WHEN r.[ConversationId] IS NOT NULL THEN SYSUTCDATETIME() END,
       N'backfill'
FROM #u797c1_cl_resolved r
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.[ReviewNotification] n
    WHERE n.[Kind] = N'contract_labor'
      AND n.[ProjectId] = r.[ProjectId]
      AND n.[WorkDate] = r.[WorkDate]);
GO

INSERT INTO dbo.[ReviewNotificationTarget]
    ([ReviewNotificationId], [BillId], [ExpenseId], [BillCreditId], [ContractLaborId])
SELECT DISTINCT n.[Id], NULL, NULL, NULL, cl.[Id]
FROM dbo.[ReviewNotification] n
JOIN dbo.[ContractLabor] cl ON cl.[WorkDate] = n.[WorkDate]
JOIN dbo.[ContractLaborLineItem] li
  ON li.[ContractLaborId] = cl.[Id]
 AND li.[ProjectId] = n.[ProjectId]
WHERE n.[Kind] = N'contract_labor'
  AND n.[Source] = N'backfill'
  AND NOT EXISTS (SELECT 1 FROM dbo.[ReviewNotificationTarget] t WHERE t.[ReviewNotificationId] = n.[Id]);
-- Reports: run scripts/u797c1_rebind_proof.py --report (read-only).
GO
