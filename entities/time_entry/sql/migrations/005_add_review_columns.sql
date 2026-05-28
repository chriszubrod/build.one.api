-- Add agent-review metadata columns to TimeEntry.
--
-- ReviewPriority: agent's flag bucket. NULL when un-reviewed.
--   Allowed values (enforced in prompt, not in CHECK constraint, so future
--   buckets can be added without a migration): 'high' | 'medium' | 'low' | 'clean'.
-- ReviewReasons: JSON array of short reason codes documenting why the agent
--   bucketed the entry. Example: '["null_project", "over_12hr"]'.
--   NULL when un-reviewed; '[]' when reviewed and clean.
--
-- Stamped by dbo.StampTimeEntryReview (migration 006) called from the
-- time_tracking_specialist agent's flag tool. No status transition,
-- no Workflow row — this is observability metadata, not a CRUD event.
--
-- Idempotent.

IF COL_LENGTH('dbo.TimeEntry', 'ReviewPriority') IS NULL
BEGIN
    ALTER TABLE dbo.[TimeEntry] ADD [ReviewPriority] VARCHAR(20) NULL;
END
GO

IF COL_LENGTH('dbo.TimeEntry', 'ReviewReasons') IS NULL
BEGIN
    ALTER TABLE dbo.[TimeEntry] ADD [ReviewReasons] NVARCHAR(MAX) NULL;
END
GO
