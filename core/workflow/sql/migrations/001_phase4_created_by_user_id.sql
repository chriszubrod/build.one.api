-- Phase 4 — Access Control Rebuild — Workflow attribution
-- Adds CreatedByUserId BIGINT NULL FK User.Id to dbo.Workflow.
-- Idempotent. Safe to re-run.
--
-- The legacy `CreatedBy` VARCHAR column stays in place as a free-text
-- "source/component" tag for non-user origins (e.g.
-- 'instant_workflow_handler', 'system'). User attribution moves to
-- the new CreatedByUserId FK column.
--
-- No backfill needed for dbo.Workflow — every existing row has
-- CreatedBy = NULL (the column was added but never populated by
-- ProcessEngine).

SET XACT_ABORT ON;
SET NOCOUNT ON;

GO

-- 1. Add CreatedByUserId column
IF OBJECT_ID('dbo.Workflow', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID('dbo.Workflow')
          AND name = 'CreatedByUserId'
   )
BEGIN
    ALTER TABLE [dbo].[Workflow]
        ADD [CreatedByUserId] BIGINT NULL;
    PRINT 'Workflow.CreatedByUserId column added.';
END
ELSE
BEGIN
    PRINT 'Workflow.CreatedByUserId column already present — skipping.';
END
GO

-- 2. FK constraint
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Workflow_CreatedByUser'
)
BEGIN
    ALTER TABLE [dbo].[Workflow]
        ADD CONSTRAINT [FK_Workflow_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
    PRINT 'FK_Workflow_CreatedByUser added.';
END
ELSE
BEGIN
    PRINT 'FK_Workflow_CreatedByUser already present — skipping.';
END
GO

-- 3. Index for "workflows-by-creator" queries
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
     WHERE name = 'IX_Workflow_CreatedByUserId'
       AND object_id = OBJECT_ID('dbo.Workflow')
)
BEGIN
    CREATE INDEX [IX_Workflow_CreatedByUserId]
        ON [dbo].[Workflow] ([CreatedByUserId])
        WHERE [CreatedByUserId] IS NOT NULL;
    PRINT 'IX_Workflow_CreatedByUserId added.';
END
ELSE
BEGIN
    PRINT 'IX_Workflow_CreatedByUserId already present — skipping.';
END
GO

PRINT 'Phase 4 Workflow.CreatedByUserId migration complete.';
