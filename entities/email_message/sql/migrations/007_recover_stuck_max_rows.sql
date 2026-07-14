-- Migration: per-tick @MaxRows cap on RecoverStuckProcessingEmailMessages
-- ------------------------------------------------------------
-- Adds @MaxRows INT = 50 so a large orphan backlog cannot be reset/failed
-- in one giant transaction on a single recovery tick. Idempotent (CREATE OR ALTER).

-- Recovery sproc: resets EmailMessage rows that are stuck in 'processing'
-- with no AgentSessionId stamped. See migrations/001_recovery_processing_reset.sql
-- for the full background.
CREATE OR ALTER PROCEDURE dbo.RecoverStuckProcessingEmailMessages
    @StaleAfterMinutes INT = 10,
    @MaxResets INT = 3,
    @MaxRows INT = 50
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Cutoff DATETIME2(3) = DATEADD(MINUTE, -@StaleAfterMinutes, @Now);
    DECLARE @ResetCount INT = 0;
    DECLARE @FailedCount INT = 0;

    UPDATE TOP (@MaxRows) dbo.[EmailMessage]
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

    UPDATE TOP (@MaxRows) dbo.[EmailMessage]
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
