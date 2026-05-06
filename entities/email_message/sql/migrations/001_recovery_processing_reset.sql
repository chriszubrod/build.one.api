-- Migration: stuck-row recovery for EmailMessage
-- ------------------------------------------------------------
-- Adds ProcessingResetCount column + RecoverStuckProcessingEmailMessages sproc.
-- Idempotent — safe to re-run.
--
-- Background: ClaimNextPendingEmailMessage commits ProcessingStatus='processing'
-- in its own transaction, then the API handler resolves the agent user + calls
-- start_run which inserts an AgentSession. EmailMessage.AgentSessionId is only
-- stamped later by the agent's mark_email_outcome tool. Any failure between
-- claim and mark_email_outcome (worker recycle, agent crash, transport error)
-- orphans the EmailMessage with ProcessingStatus='processing', AgentSessionId
-- IS NULL, and no recovery path — the next process_one tick won't re-claim it
-- because its status isn't 'pending'.
--
-- This sproc resets such orphans back to 'pending' (with a retry budget) so
-- they can be reprocessed. Companion intelligence/.../001_timeout_long_running_sessions.sql
-- handles the OTHER case (AgentSession started but never finalized).

-- 1. Add the retry-counter column. Idempotent.
IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns WHERE Name = 'ProcessingResetCount' AND Object_ID = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage]
        ADD [ProcessingResetCount] INT NOT NULL
            CONSTRAINT [DF_EmailMessage_ProcessingResetCount] DEFAULT (0);
END
GO

-- 2. Recovery sproc.
CREATE OR ALTER PROCEDURE dbo.RecoverStuckProcessingEmailMessages
    @StaleAfterMinutes INT = 10,
    @MaxResets INT = 3
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Cutoff DATETIME2(3) = DATEADD(MINUTE, -@StaleAfterMinutes, @Now);
    DECLARE @ResetCount INT = 0;
    DECLARE @FailedCount INT = 0;

    -- Branch A: rows under the retry budget → reset to 'pending'.
    UPDATE dbo.[EmailMessage]
    SET [ProcessingStatus] = 'pending',
        [ProcessingResetCount] = [ProcessingResetCount] + 1,
        [LastError] = CONCAT(
            'auto-reset by recovery cron (reset #',
            [ProcessingResetCount] + 1,
            ', stale ', DATEDIFF(MINUTE, [ModifiedDatetime], @Now), ' min)'
        ),
        [ModifiedDatetime] = @Now
    WHERE [ProcessingStatus] = 'processing'
      AND [AgentSessionId] IS NULL
      AND [ModifiedDatetime] < @Cutoff
      AND [ProcessingResetCount] < @MaxResets;
    SET @ResetCount = @@ROWCOUNT;

    -- Branch B: rows that have exhausted the retry budget → dead-letter to 'failed'.
    UPDATE dbo.[EmailMessage]
    SET [ProcessingStatus] = 'failed',
        [LastError] = CONCAT(
            'auto-failed after ', [ProcessingResetCount], ' resets ',
            '(stuck in processing without AgentSessionId)'
        ),
        [ModifiedDatetime] = @Now
    WHERE [ProcessingStatus] = 'processing'
      AND [AgentSessionId] IS NULL
      AND [ModifiedDatetime] < @Cutoff
      AND [ProcessingResetCount] >= @MaxResets;
    SET @FailedCount = @@ROWCOUNT;

    SELECT @ResetCount AS ResetCount, @FailedCount AS FailedCount;
END;
GO
