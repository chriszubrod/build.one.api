-- =============================================================================
-- U-797c1a data correction: clear the draft identity on ReviewNotification 1089.
--
-- Why: the MR2-MAIN 10/7 Contract Labor draft was retitled and sent as HP2 10/7.
-- Its ConversationId, InternetMessageId and DraftGraphMessageId stamp the HP2
-- thread, yet this record still says MR2-MAIN. Replies on that thread then fail
-- on wrong_project_or_date. With no conversation on the record, they bind through
-- the legacy resolver instead (LEGACY_BIND).
--
-- One row only. The WHERE clause matches this record while it still carries a
-- ConversationId, so a re-run changes nothing. No other table is touched.
-- Run from the repo root:
--   ./.venv/bin/python scripts/run_sql.py scripts/migrations/2026_11_21_u797c1a_clear_repurposed_draft_conversation.sql
-- =============================================================================

-- Re-stamp: this backfilled record has OutboxId NULL, and the worker stamp and
-- SetReviewNotificationOutbox both match on OutboxId, so nothing can put the HP2
-- conversation back on it. (U-797c1a review S2, checked on prod 2026-11-21.)
UPDATE dbo.[ReviewNotification]
SET [ConversationId]      = NULL,
    [InternetMessageId]   = NULL,
    [DraftGraphMessageId] = NULL
WHERE [Id] = 1089
  AND [Kind] = N'contract_labor'
  AND [Subject] = N'Contract Labor - MR2-MAIN - 2026-10-07'
  AND [ProjectId] = 93
  AND [WorkDate] = '2026-10-07'
  AND [ConversationId] IS NOT NULL;
GO

SELECT [Id],
       CASE WHEN [ConversationId] IS NULL THEN 1 ELSE 0 END AS ConversationIsNull
FROM dbo.[ReviewNotification]
WHERE [Id] = 1089;
GO
