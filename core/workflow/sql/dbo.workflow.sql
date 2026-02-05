-- =============================================================================
-- Workflow Table with Stored Procedures (dbo schema)
-- =============================================================================

DROP TABLE IF EXISTS [dbo].[Workflow];
GO

CREATE TABLE [dbo].[Workflow]
(
    -- Standard first 5 columns
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Tenant isolation
    [TenantId] BIGINT NOT NULL,

    -- Type and state
    [WorkflowType] VARCHAR(50) NOT NULL,
    [State] VARCHAR(50) NOT NULL,

    -- Parent/child relationship
    [ParentWorkflowId] BIGINT NULL,

    -- Correlation keys
    [ConversationId] NVARCHAR(200) NULL,
    [TriggerMessageId] NVARCHAR(200) NULL,

    -- Entity references
    [VendorId] BIGINT NULL,
    [ProjectId] BIGINT NULL,
    [BillId] BIGINT NULL,

    -- Audit
    [CreatedBy] VARCHAR(200) NULL,

    -- Flexible context
    [Context] NVARCHAR(MAX) NULL,

    -- Completion timestamp
    [CompletedDatetime] DATETIME2(3) NULL,

    CONSTRAINT [UQ_Workflow_PublicId] UNIQUE ([PublicId]),
    CONSTRAINT [FK_Workflow_Parent] FOREIGN KEY ([ParentWorkflowId])
        REFERENCES [dbo].[Workflow]([Id])
);
GO

CREATE INDEX IX_Workflow_TenantState ON [dbo].[Workflow]([TenantId], [State]);
CREATE INDEX IX_Workflow_ConversationId ON [dbo].[Workflow]([ConversationId]);
CREATE INDEX IX_Workflow_ParentWorkflowId ON [dbo].[Workflow]([ParentWorkflowId]);
CREATE INDEX IX_Workflow_TriggerMessageId ON [dbo].[Workflow]([TriggerMessageId]);
CREATE INDEX IX_Workflow_VendorId ON [dbo].[Workflow]([VendorId]);
GO


-- =============================================================================
-- Stored Procedures
-- =============================================================================

DROP PROCEDURE IF EXISTS CreateWorkflow;
GO

CREATE PROCEDURE CreateWorkflow
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
    @Context NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Workflow] (
        [CreatedDatetime], [ModifiedDatetime], [TenantId], [WorkflowType], [State],
        [ParentWorkflowId], [ConversationId], [TriggerMessageId],
        [VendorId], [ProjectId], [BillId], [CreatedBy], [Context]
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
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime]
    VALUES (
        @Now, @Now, @TenantId, @WorkflowType, @State,
        @ParentWorkflowId, @ConversationId, @TriggerMessageId,
        @VendorId, @ProjectId, @BillId, @CreatedBy, @Context
    );

    COMMIT TRANSACTION;
END;
GO




DROP PROCEDURE IF EXISTS ReadWorkflowById;
GO

CREATE PROCEDURE ReadWorkflowById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO




DROP PROCEDURE IF EXISTS ReadWorkflowByPublicId;
GO

CREATE PROCEDURE ReadWorkflowByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO




DROP PROCEDURE IF EXISTS ReadWorkflowsByConversationId;
GO

CREATE PROCEDURE ReadWorkflowsByConversationId
(
    @ConversationId NVARCHAR(200)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [ConversationId] = @ConversationId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





DROP PROCEDURE IF EXISTS ReadWorkflowByTriggerMessageId;
GO

CREATE PROCEDURE ReadWorkflowByTriggerMessageId
(
    @TriggerMessageId NVARCHAR(200)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [TriggerMessageId] = @TriggerMessageId;

    COMMIT TRANSACTION;
END;
GO



-- Duplicate check: return workflow by trigger_message_id AND workflow_type (so we get email_intake, not bill_processing)
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





DROP PROCEDURE IF EXISTS ReadWorkflowsByTenantAndState;
GO

CREATE PROCEDURE ReadWorkflowsByTenantAndState
(
    @TenantId BIGINT,
    @State VARCHAR(50) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF @State IS NULL
        SELECT
            w.[Id],
            w.[PublicId],
            w.[RowVersion],
            CONVERT(VARCHAR(19), w.[CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), w.[ModifiedDatetime], 120) AS [ModifiedDatetime],
            w.[TenantId],
            w.[WorkflowType],
            w.[State],
            w.[ParentWorkflowId],
            w.[ConversationId],
            w.[TriggerMessageId],
            w.[VendorId],
            w.[ProjectId],
            w.[BillId],
            w.[CreatedBy],
            w.[Context],
            CONVERT(VARCHAR(19), w.[CompletedDatetime], 120) AS [CompletedDatetime]
        FROM dbo.[Workflow] w
        WHERE w.[TenantId] = @TenantId
          -- Exclude completed parent workflows that have children
          AND NOT (w.[State] = 'completed' AND EXISTS (
              SELECT 1 FROM dbo.[Workflow] child WHERE child.[ParentWorkflowId] = w.[Id]
          ))
        ORDER BY w.[CreatedDatetime] DESC;
    ELSE
        SELECT
            w.[Id],
            w.[PublicId],
            w.[RowVersion],
            CONVERT(VARCHAR(19), w.[CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), w.[ModifiedDatetime], 120) AS [ModifiedDatetime],
            w.[TenantId],
            w.[WorkflowType],
            w.[State],
            w.[ParentWorkflowId],
            w.[ConversationId],
            w.[TriggerMessageId],
            w.[VendorId],
            w.[ProjectId],
            w.[BillId],
            w.[CreatedBy],
            w.[Context],
            CONVERT(VARCHAR(19), w.[CompletedDatetime], 120) AS [CompletedDatetime]
        FROM dbo.[Workflow] w
        WHERE w.[TenantId] = @TenantId
          AND w.[State] = @State
          -- Exclude completed parent workflows that have children
          AND NOT (@State = 'completed' AND EXISTS (
              SELECT 1 FROM dbo.[Workflow] child WHERE child.[ParentWorkflowId] = w.[Id]
          ))
        ORDER BY w.[CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





DROP PROCEDURE IF EXISTS ReadActiveWorkflows;
GO

CREATE PROCEDURE ReadActiveWorkflows
(
    @TenantId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [TenantId] = @TenantId
      AND [State] NOT IN ('completed', 'abandoned', 'cancelled')
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





DROP PROCEDURE IF EXISTS ReadChildWorkflows;
GO

CREATE PROCEDURE ReadChildWorkflows
(
    @ParentWorkflowId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [ParentWorkflowId] = @ParentWorkflowId
    ORDER BY [CreatedDatetime];

    COMMIT TRANSACTION;
END;
GO






DROP PROCEDURE IF EXISTS UpdateWorkflowState;
GO

CREATE PROCEDURE UpdateWorkflowState
(
    @PublicId UNIQUEIDENTIFIER,
    @State VARCHAR(50),
    @Context NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Workflow]
    SET
        [State] = @State,
        [Context] = COALESCE(@Context, [Context]),
        [ModifiedDatetime] = @Now,
        [CompletedDatetime] = CASE WHEN @State IN ('completed', 'abandoned', 'cancelled') THEN @Now ELSE [CompletedDatetime] END
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
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO







DROP PROCEDURE IF EXISTS UpdateWorkflowEntities;
GO

CREATE PROCEDURE UpdateWorkflowEntities
(
    @PublicId UNIQUEIDENTIFIER,
    @VendorId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @BillId BIGINT = NULL,
    @Context NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Workflow]
    SET
        [VendorId] = COALESCE(@VendorId, [VendorId]),
        [ProjectId] = COALESCE(@ProjectId, [ProjectId]),
        [BillId] = COALESCE(@BillId, [BillId]),
        [Context] = COALESCE(@Context, [Context]),
        [ModifiedDatetime] = @Now
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
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO






DROP PROCEDURE IF EXISTS UpdateWorkflowContext;
GO

CREATE PROCEDURE UpdateWorkflowContext
(
    @PublicId UNIQUEIDENTIFIER,
    @Context NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Workflow]
    SET
        [Context] = @Context,
        [ModifiedDatetime] = @Now
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
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO





DROP PROCEDURE IF EXISTS ReadWorkflowsPastTimeout;
GO

CREATE PROCEDURE ReadWorkflowsPastTimeout
(
    @TenantId BIGINT,
    @State VARCHAR(50),
    @TimeoutDays INT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [TenantId] = @TenantId
      AND [State] = @State
      AND [ModifiedDatetime] < DATEADD(DAY, -@TimeoutDays, SYSUTCDATETIME())
    ORDER BY [ModifiedDatetime];

    COMMIT TRANSACTION;
END;
GO





DROP PROCEDURE IF EXISTS ReadWorkflowsCreatedBetween;
GO

CREATE PROCEDURE ReadWorkflowsCreatedBetween
(
    @TenantId BIGINT,
    @StartDate DATETIME2,
    @EndDate DATETIME2
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [TenantId] = @TenantId
      AND [CreatedDatetime] >= @StartDate
      AND [CreatedDatetime] < @EndDate
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





DROP PROCEDURE IF EXISTS ReadWorkflowsCompletedBetween;
GO

CREATE PROCEDURE ReadWorkflowsCompletedBetween
(
    @TenantId BIGINT,
    @StartDate DATETIME2,
    @EndDate DATETIME2
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
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
    WHERE [TenantId] = @TenantId
      AND [CompletedDatetime] >= @StartDate
      AND [CompletedDatetime] < @EndDate
    ORDER BY [CompletedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO
