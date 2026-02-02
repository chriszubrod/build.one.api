-- =============================================================================
-- WorkflowEvent Table with Stored Procedures (dbo schema)
-- =============================================================================

DROP TABLE IF EXISTS [dbo].[WorkflowEvent];
GO

CREATE TABLE [dbo].[WorkflowEvent]
(
    -- Standard first 5 columns
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Parent workflow
    [WorkflowId] BIGINT NOT NULL,

    -- Event details
    [EventType] VARCHAR(50) NOT NULL,
    [FromState] VARCHAR(50) NULL,
    [ToState] VARCHAR(50) NULL,
    [StepName] VARCHAR(100) NULL,

    -- Event data
    [Data] NVARCHAR(MAX) NULL,

    -- Audit
    [CreatedBy] VARCHAR(200) NULL,

    CONSTRAINT [UQ_WorkflowEvent_PublicId] UNIQUE ([PublicId]),
    CONSTRAINT [FK_WorkflowEvent_Workflow] FOREIGN KEY ([WorkflowId])
        REFERENCES [dbo].[Workflow]([Id]) ON DELETE CASCADE
);
GO

CREATE INDEX IX_WorkflowEvent_WorkflowId ON [dbo].[WorkflowEvent]([WorkflowId], [CreatedDatetime]);
CREATE INDEX IX_WorkflowEvent_EventType ON [dbo].[WorkflowEvent]([EventType], [CreatedDatetime]);
GO


-- =============================================================================
-- Stored Procedures
-- =============================================================================




DROP PROCEDURE IF EXISTS CreateWorkflowEvent;
GO

CREATE PROCEDURE CreateWorkflowEvent
(
    @WorkflowId BIGINT,
    @EventType VARCHAR(50),
    @FromState VARCHAR(50) = NULL,
    @ToState VARCHAR(50) = NULL,
    @StepName VARCHAR(100) = NULL,
    @Data NVARCHAR(MAX) = NULL,
    @CreatedBy VARCHAR(200) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[WorkflowEvent] (
        [CreatedDatetime], [ModifiedDatetime], [WorkflowId],
        [EventType], [FromState], [ToState], [StepName], [Data], [CreatedBy]
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
        INSERTED.[CreatedBy]
    VALUES (
        @Now, @Now, @WorkflowId,
        @EventType, @FromState, @ToState, @StepName, @Data, @CreatedBy
    );

    COMMIT TRANSACTION;
END;
GO






DROP PROCEDURE IF EXISTS ReadWorkflowEventsByWorkflowId;
GO

CREATE PROCEDURE ReadWorkflowEventsByWorkflowId
(
    @WorkflowId BIGINT
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
        [WorkflowId],
        [EventType],
        [FromState],
        [ToState],
        [StepName],
        [Data],
        [CreatedBy]
    FROM dbo.[WorkflowEvent]
    WHERE [WorkflowId] = @WorkflowId
    ORDER BY [CreatedDatetime];

    COMMIT TRANSACTION;
END;
GO







DROP PROCEDURE IF EXISTS ReadWorkflowEventsByType;
GO

CREATE PROCEDURE ReadWorkflowEventsByType
(
    @WorkflowId BIGINT,
    @EventType VARCHAR(50)
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
        [WorkflowId],
        [EventType],
        [FromState],
        [ToState],
        [StepName],
        [Data],
        [CreatedBy]
    FROM dbo.[WorkflowEvent]
    WHERE [WorkflowId] = @WorkflowId AND [EventType] = @EventType
    ORDER BY [CreatedDatetime];

    COMMIT TRANSACTION;
END;
GO






DROP PROCEDURE IF EXISTS ReadLatestWorkflowEvent;
GO

CREATE PROCEDURE ReadLatestWorkflowEvent
(
    @WorkflowId BIGINT
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
        [WorkflowId],
        [EventType],
        [FromState],
        [ToState],
        [StepName],
        [Data],
        [CreatedBy]
    FROM dbo.[WorkflowEvent]
    WHERE [WorkflowId] = @WorkflowId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





DROP PROCEDURE IF EXISTS ReadWorkflowEventsInRange;
GO

CREATE PROCEDURE ReadWorkflowEventsInRange
(
    @StartDate DATETIME2,
    @EndDate DATETIME2,
    @EventType VARCHAR(50) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF @EventType IS NULL
        SELECT
            [Id],
            [PublicId],
            [RowVersion],
            CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
            [WorkflowId],
            [EventType],
            [FromState],
            [ToState],
            [StepName],
            [Data],
            [CreatedBy]
        FROM dbo.[WorkflowEvent]
        WHERE [CreatedDatetime] >= @StartDate AND [CreatedDatetime] < @EndDate
        ORDER BY [CreatedDatetime];
    ELSE
        SELECT
            [Id],
            [PublicId],
            [RowVersion],
            CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
            [WorkflowId],
            [EventType],
            [FromState],
            [ToState],
            [StepName],
            [Data],
            [CreatedBy]
        FROM dbo.[WorkflowEvent]
        WHERE [CreatedDatetime] >= @StartDate AND [CreatedDatetime] < @EndDate AND [EventType] = @EventType
        ORDER BY [CreatedDatetime];

    COMMIT TRANSACTION;
END;
GO
