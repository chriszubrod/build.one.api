-- Phase 4 — Access Control Rebuild — CreateWorkflow attribution param
-- Adds @CreatedByUserId BIGINT = NULL to CreateWorkflow and stores
-- it on the new column. Idempotent (CREATE OR ALTER).
--
-- The legacy @CreatedBy VARCHAR param stays as a free-text
-- source/component tag.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

CREATE OR ALTER PROCEDURE CreateWorkflow
(
    @TenantId BIGINT,
    @WorkflowType VARCHAR(50),
    @State VARCHAR(50),
    @ParentWorkflowId BIGINT = NULL,
    @ConversationId NVARCHAR(200) = NULL,
    @TriggerMessageId NVARCHAR(200) = NULL,
    @VendorId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @BillId BIGINT = NULL,
    @CreatedBy VARCHAR(200) = NULL,
    @Context NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Workflow] (
        [CreatedDatetime], [ModifiedDatetime], [TenantId], [WorkflowType], [State],
        [ParentWorkflowId], [ConversationId], [TriggerMessageId],
        [VendorId], [ProjectId], [BillId], [CreatedBy], [Context],
        [CreatedByUserId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[WorkflowType],
        INSERTED.[State],
        INSERTED.[ParentWorkflowId],
        INSERTED.[ConversationId],
        INSERTED.[TriggerMessageId],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[BillId],
        INSERTED.[CreatedBy],
        INSERTED.[Context],
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime],
        INSERTED.[CreatedByUserId]
    VALUES (
        @Now, @Now, @TenantId, @WorkflowType, @State,
        @ParentWorkflowId, @ConversationId, @TriggerMessageId,
        @VendorId, @ProjectId, @BillId, @CreatedBy, @Context,
        @CreatedByUserId
    );

    COMMIT TRANSACTION;
END;
GO

PRINT 'CreateWorkflow extended with @CreatedByUserId.';
