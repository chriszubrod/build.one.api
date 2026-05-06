-- Migration: timeout sproc for long-running AgentSessions
-- ------------------------------------------------------------
-- Adds intelligence-side TimeoutLongRunningAgentSessions sproc. Idempotent.
--
-- Background: AgentSession rows can get stuck in Status='running' indefinitely
-- when the worker process is recycled mid-run, the agent crashes, or a tool
-- call hangs without termination (the existing 10-min SSE safety cap only
-- applies to the streaming layer, not the session row). Existing example:
-- AgentSession Id=124 (scout) has been 'running' since 2026-04-25.
--
-- This sproc finalizes such sessions to Status='failed' with a clear
-- TerminationReason. For sessions linked to an EmailMessage that is also
-- stuck in 'processing', it resets the EmailMessage back to 'pending' (with
-- a retry budget; falls through to 'failed' once exhausted) so the queue
-- drainer can re-attempt.
--
-- Companion entities/email_message/.../001_recovery_processing_reset.sql
-- handles EmailMessage rows that orphaned BEFORE an AgentSessionId was
-- stamped (the other failure mode).

CREATE OR ALTER PROCEDURE dbo.TimeoutLongRunningAgentSessions
    @StaleAfterMinutes INT = 30,
    @MaxEmailResets INT = 3
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Cutoff DATETIME2(3) = DATEADD(MINUTE, -@StaleAfterMinutes, @Now);

    -- Capture which sessions we time out so we can fix their linked EmailMessage rows.
    DECLARE @TimedOut TABLE (Id BIGINT);

    -- 1. Time out stuck sessions. "Stuck" = Status='running' and no recent
    --    progress (ModifiedDatetime if set, else StartedAt).
    UPDATE s
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

    -- 2a. Linked EmailMessage rows under the retry budget → reset to 'pending'.
    --     Clears AgentSessionId so the recovery picks up cleanly on the next
    --     claim cycle.
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

    -- 2b. Linked EmailMessage rows over budget → dead-letter to 'failed'.
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
