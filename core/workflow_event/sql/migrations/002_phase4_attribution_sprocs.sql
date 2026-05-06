-- Phase 4 — Access Control Rebuild — CreateWorkflowEvent attribution param
-- Adds @CreatedByUserId BIGINT = NULL to CreateWorkflowEvent and
-- stores it on the new column. Idempotent (CREATE OR ALTER).
--
-- The legacy @CreatedBy VARCHAR param stays as a free-text
-- source/component tag (e.g. 'instant_workflow_handler', 'system').

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

CREATE OR ALTER PROCEDURE CreateWorkflowEvent
(
    @WorkflowId BIGINT,
    @EventType VARCHAR(50),
    @FromState VARCHAR(50) = NULL,
    @ToState VARCHAR(50) = NULL,
    @StepName VARCHAR(100) = NULL,
    @Data NVARCHAR(MAX) = NULL,
    @CreatedBy VARCHAR(200) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[WorkflowEvent] (
        [CreatedDatetime], [ModifiedDatetime], [WorkflowId],
        [EventType], [FromState], [ToState], [StepName], [Data], [CreatedBy],
        [CreatedByUserId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[WorkflowId],
        INSERTED.[EventType],
        INSERTED.[FromState],
        INSERTED.[ToState],
        INSERTED.[StepName],
        INSERTED.[Data],
        INSERTED.[CreatedBy],
        INSERTED.[CreatedByUserId]
    VALUES (
        @Now, @Now, @WorkflowId,
        @EventType, @FromState, @ToState, @StepName, @Data, @CreatedBy,
        @CreatedByUserId
    );

    COMMIT TRANSACTION;
END;
GO

PRINT 'CreateWorkflowEvent extended with @CreatedByUserId.';
