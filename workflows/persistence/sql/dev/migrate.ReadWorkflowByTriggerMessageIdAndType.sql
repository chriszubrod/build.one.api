-- Add ReadWorkflowByTriggerMessageIdAndType for correct duplicate detection
-- (so we get email_intake workflow, not bill_processing child, when checking for duplicate).
-- Run this if duplicate detection returns the wrong workflow or creates extra email_intake workflows.

DROP PROCEDURE IF EXISTS ReadWorkflowByTriggerMessageIdAndType;
GO

CREATE PROCEDURE ReadWorkflowByTriggerMessageIdAndType
(
    @TriggerMessageId NVARCHAR(200),
    @WorkflowType NVARCHAR(100)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [TenantId],
        [WorkflowType],
        [State],
        [ParentWorkflowId],
        [ConversationId],
        [TriggerMessageId],
        [VendorId],
        [ProjectId],
        [BillId],
        [CreatedBy],
        [Context],
        CONVERT(VARCHAR(19), [CompletedDatetime], 120) AS [CompletedDatetime]
    FROM dbo.[Workflow]
    WHERE [TriggerMessageId] = @TriggerMessageId AND [WorkflowType] = @WorkflowType;

    COMMIT TRANSACTION;
END;
GO
