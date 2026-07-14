-- Migration: per-tick @MaxRows cap on TimeoutLongRunningAgentSessions
-- ------------------------------------------------------------
-- Adds @MaxRows INT = 50 so a large backlog of stuck sessions cannot be
-- timed out in one giant transaction on a single recovery tick. Idempotent (CREATE OR ALTER).

-- Recovery sproc: times out long-running AgentSessions and resets any
-- linked EmailMessage rows so they can be re-processed. See
-- migrations/001_timeout_long_running_sessions.sql for the full background.
CREATE OR ALTER PROCEDURE dbo.TimeoutLongRunningAgentSessions
    @StaleAfterMinutes INT = 30,
    @MaxEmailResets INT = 3,
    @MaxRows INT = 50
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Cutoff DATETIME2(3) = DATEADD(MINUTE, -@StaleAfterMinutes, @Now);

    DECLARE @TimedOut TABLE (Id BIGINT);

    UPDATE TOP (@MaxRows) s
    SET [Status] = 'failed',
        [TerminationReason] = 'auto-timeout (recovery cron)',
        [ErrorMessage] = CONCAT(
            'auto-timed-out after ',
            DATEDIFF(MINUTE, COALESCE(s.[ModifiedDatetime], s.[StartedAt]), @Now),
            ' min idle'
        ),
        [CompletedAt] = @Now,
        [ModifiedDatetime] = @Now
    OUTPUT INSERTED.[Id] INTO @TimedOut(Id)
    FROM dbo.[AgentSession] s
    WHERE s.[Status] = 'running'
      AND COALESCE(s.[ModifiedDatetime], s.[StartedAt]) < @Cutoff;

    DECLARE @TimedOutCount INT = (SELECT COUNT(*) FROM @TimedOut);

    DECLARE @EmailResetCount INT = 0;
    UPDATE em
    SET [ProcessingStatus] = 'pending',
        [AgentSessionId] = NULL,
        [ProcessingResetCount] = em.[ProcessingResetCount] + 1,
        [LastError] = CONCAT(
            'agent session auto-timed-out (reset #',
            em.[ProcessingResetCount] + 1, ')'
        ),
        [ModifiedDatetime] = @Now
    FROM dbo.[EmailMessage] em
    INNER JOIN @TimedOut t ON t.Id = em.[AgentSessionId]
    WHERE em.[ProcessingStatus] = 'processing'
      AND em.[ProcessingResetCount] < @MaxEmailResets;
    SET @EmailResetCount = @@ROWCOUNT;

    DECLARE @EmailFailedCount INT = 0;
    UPDATE em
    SET [ProcessingStatus] = 'failed',
        [LastError] = CONCAT(
            'auto-failed after ', em.[ProcessingResetCount],
            ' resets (final attempt timed out)'
        ),
        [ModifiedDatetime] = @Now
    FROM dbo.[EmailMessage] em
    INNER JOIN @TimedOut t ON t.Id = em.[AgentSessionId]
    WHERE em.[ProcessingStatus] = 'processing'
      AND em.[ProcessingResetCount] >= @MaxEmailResets;
    SET @EmailFailedCount = @@ROWCOUNT;

    SELECT
        @TimedOutCount     AS TimedOutSessionCount,
        @EmailResetCount   AS LinkedEmailResetCount,
        @EmailFailedCount  AS LinkedEmailFailedCount;
END;
GO
