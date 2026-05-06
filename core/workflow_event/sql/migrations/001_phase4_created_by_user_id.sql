-- Phase 4 — Access Control Rebuild — WorkflowEvent attribution
-- Adds CreatedByUserId BIGINT NULL FK User.Id to dbo.WorkflowEvent
-- and backfills existing 'user:{id}' string values into the new
-- column.
--
-- Idempotent. Safe to re-run.
--
-- The legacy `CreatedBy` VARCHAR column stays in place as a free-text
-- "source/component" tag for non-user origins (e.g.
-- 'instant_workflow_handler', 'system', 'email_intake_executor').
-- User attribution moves to the new CreatedByUserId FK column.
--
-- Legacy agent-name tags ('email_triage_agent', 'email_intake_executor')
-- are NOT backfilled — modern agent code writes CreatedByUserId from
-- the agent's authenticated User row.

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO

-- 1. Add CreatedByUserId column
IF OBJECT_ID('dbo.WorkflowEvent', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID('dbo.WorkflowEvent')
          AND name = 'CreatedByUserId'
   )
BEGIN
    ALTER TABLE [dbo].[WorkflowEvent]
        ADD [CreatedByUserId] BIGINT NULL;
    PRINT 'WorkflowEvent.CreatedByUserId column added.';
END
ELSE
BEGIN
    PRINT 'WorkflowEvent.CreatedByUserId column already present — skipping.';
END
GO

-- 2. FK constraint
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_WorkflowEvent_CreatedByUser'
)
BEGIN
    ALTER TABLE [dbo].[WorkflowEvent]
        ADD CONSTRAINT [FK_WorkflowEvent_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
    PRINT 'FK_WorkflowEvent_CreatedByUser added.';
END
ELSE
BEGIN
    PRINT 'FK_WorkflowEvent_CreatedByUser already present — skipping.';
END
GO

-- 3. Index
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
     WHERE name = 'IX_WorkflowEvent_CreatedByUserId'
       AND object_id = OBJECT_ID('dbo.WorkflowEvent')
)
BEGIN
    CREATE INDEX [IX_WorkflowEvent_CreatedByUserId]
        ON [dbo].[WorkflowEvent] ([CreatedByUserId])
        WHERE [CreatedByUserId] IS NOT NULL;
    PRINT 'IX_WorkflowEvent_CreatedByUserId added.';
END
ELSE
BEGIN
    PRINT 'IX_WorkflowEvent_CreatedByUserId already present — skipping.';
END
GO

-- 4. Backfill: parse existing 'user:{id}' format → CreatedByUserId.
--    Idempotent: only updates rows where CreatedByUserId IS NULL and
--    CreatedBy matches the user:{id} pattern AND the parsed id is a
--    valid User.Id.
UPDATE we
   SET [CreatedByUserId] = TRY_CAST(SUBSTRING(we.[CreatedBy], 6, 50) AS BIGINT)
  FROM dbo.[WorkflowEvent] we
 WHERE we.[CreatedByUserId] IS NULL
   AND we.[CreatedBy] LIKE 'user:%'
   AND TRY_CAST(SUBSTRING(we.[CreatedBy], 6, 50) AS BIGINT) IS NOT NULL
   AND EXISTS (
       SELECT 1 FROM dbo.[User] u
        WHERE u.[Id] = TRY_CAST(SUBSTRING(we.[CreatedBy], 6, 50) AS BIGINT)
   );

DECLARE @rows INT = @@ROWCOUNT;
PRINT CONCAT('WorkflowEvent backfill: ', @rows, ' user-attribution row(s) populated.');
GO

PRINT 'Phase 4 WorkflowEvent.CreatedByUserId migration complete.';
