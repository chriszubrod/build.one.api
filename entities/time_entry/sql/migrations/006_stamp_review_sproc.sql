-- StampTimeEntryReview — set ReviewPriority + ReviewReasons on a TimeEntry.
--
-- Called by the time_tracking_specialist agent's flag tool to record its
-- bucketing decision. Does NOT transition CurrentStatus and does NOT write
-- a Workflow / WorkflowEvent row — flag metadata is observability, not a
-- state transition. (Decision: 2026-05-26 refinement, see
-- project_time_tracking_specialist.md.)
--
-- ModifiedDatetime is intentionally NOT touched. CRUD activity on the entry
-- itself is what should bump ModifiedDatetime; an automated review stamp is
-- a sidecar.
--
-- @ReasonsJson is opaque to this sproc — caller passes a JSON string,
-- typically a short-code array like '["null_project","over_12hr"]', or '[]'
-- for clean entries. No JSON validation here; the agent is the producer.
--
-- Idempotent (UPDATE by PublicId; safe to re-run with the same payload).

CREATE OR ALTER PROCEDURE [dbo].[StampTimeEntryReview]
(
    @TimeEntryPublicId UNIQUEIDENTIFIER,
    @Priority          VARCHAR(20),
    @ReasonsJson       NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [dbo].[TimeEntry]
    SET [ReviewPriority] = @Priority,
        [ReviewReasons]  = @ReasonsJson
    WHERE [PublicId] = @TimeEntryPublicId;

    SELECT @@ROWCOUNT AS [AffectedRowCount];
END;
GO
