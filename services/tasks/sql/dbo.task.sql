-- =============================================================================
-- Task Table with Stored Procedures (dbo schema)
-- =============================================================================

DROP TABLE IF EXISTS [dbo].[Task];
GO

CREATE TABLE [dbo].[Task]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [TenantId] BIGINT NOT NULL,
    [TaskType] VARCHAR(50) NOT NULL,
    [ReferenceId] BIGINT NOT NULL,
    [Title] NVARCHAR(500) NULL,
    [Status] NVARCHAR(50) NULL,
    [SourceType] NVARCHAR(50) NULL,
    [SourceId] NVARCHAR(200) NULL,
    [Description] NVARCHAR(MAX) NULL,
    [CreatedByUserId] BIGINT NULL,
    [WorkflowId] INT NULL,
    [VendorId] BIGINT NULL,
    [ProjectId] BIGINT NULL,
    [BillId] BIGINT NULL,
    [Context] NVARCHAR(MAX) NULL,

    CONSTRAINT [UQ_Task_PublicId] UNIQUE ([PublicId]),
    CONSTRAINT [UQ_Task_TenantTypeReference] UNIQUE ([TenantId], [TaskType], [ReferenceId])
);
GO

CREATE INDEX IX_Task_TenantId ON [dbo].[Task]([TenantId]);
CREATE INDEX IX_Task_TaskType ON [dbo].[Task]([TaskType]);
CREATE INDEX IX_Task_Status ON [dbo].[Task]([Status]);
CREATE INDEX IX_Task_SourceTypeSourceId ON [dbo].[Task]([SourceType], [SourceId]);
CREATE INDEX IX_Task_WorkflowId ON [dbo].[Task]([WorkflowId]);
GO


-- =============================================================================
-- Stored Procedures
-- =============================================================================

DROP PROCEDURE IF EXISTS CreateTask;
GO

CREATE PROCEDURE CreateTask
(
    @TenantId BIGINT,
    @TaskType VARCHAR(50),
    @ReferenceId BIGINT,
    @Title NVARCHAR(500) = NULL,
    @Status NVARCHAR(50) = NULL,
    @SourceType NVARCHAR(50) = NULL,
    @SourceId NVARCHAR(200) = NULL,
    @Description NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT = NULL,
    @WorkflowId INT = NULL,
    @VendorId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @BillId BIGINT = NULL,
    @Context NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Task] (
        [CreatedDatetime], [ModifiedDatetime], [TenantId], [TaskType], [ReferenceId],
        [Title], [Status], [SourceType], [SourceId], [Description], [CreatedByUserId],
        [WorkflowId], [VendorId], [ProjectId], [BillId], [Context]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[TaskType],
        INSERTED.[ReferenceId],
        INSERTED.[Title],
        INSERTED.[Status],
        INSERTED.[SourceType],
        INSERTED.[SourceId],
        INSERTED.[Description],
        INSERTED.[CreatedByUserId],
        INSERTED.[WorkflowId],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[BillId],
        INSERTED.[Context]
    VALUES (
        @Now, @Now, @TenantId, @TaskType, @ReferenceId,
        @Title, @Status, @SourceType, @SourceId, @Description, @CreatedByUserId,
        @WorkflowId, @VendorId, @ProjectId, @BillId, @Context
    );

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadTaskByPublicId;
GO

CREATE PROCEDURE ReadTaskByPublicId
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
        [TaskType],
        [ReferenceId],
        [Title],
        [Status],
        [SourceType],
        [SourceId],
        [Description],
        [CreatedByUserId],
        [WorkflowId],
        [VendorId],
        [ProjectId],
        [BillId],
        [Context]
    FROM dbo.[Task]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadTaskById;
GO

CREATE PROCEDURE ReadTaskById
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
        [TaskType],
        [ReferenceId],
        [Title],
        [Status],
        [SourceType],
        [SourceId],
        [Description],
        [CreatedByUserId],
        [WorkflowId],
        [VendorId],
        [ProjectId],
        [BillId],
        [Context]
    FROM dbo.[Task]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadTaskByTaskTypeAndReferenceId;
GO

CREATE PROCEDURE ReadTaskByTaskTypeAndReferenceId
(
    @TenantId BIGINT,
    @TaskType VARCHAR(50),
    @ReferenceId BIGINT
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
        [TaskType],
        [ReferenceId],
        [Title],
        [Status],
        [SourceType],
        [SourceId],
        [Description],
        [CreatedByUserId],
        [WorkflowId],
        [VendorId],
        [ProjectId],
        [BillId],
        [Context]
    FROM dbo.[Task]
    WHERE [TenantId] = @TenantId
      AND [TaskType] = @TaskType
      AND [ReferenceId] = @ReferenceId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadTasks;
GO

CREATE PROCEDURE ReadTasks
(
    @TenantId BIGINT,
    @Status NVARCHAR(50) = NULL,
    @SourceType NVARCHAR(50) = NULL,
    @SourceId NVARCHAR(200) = NULL,
    @OpenOnly BIT = 0
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
        [TaskType],
        [ReferenceId],
        [Title],
        [Status],
        [SourceType],
        [SourceId],
        [Description],
        [CreatedByUserId],
        [WorkflowId],
        [VendorId],
        [ProjectId],
        [BillId],
        [Context]
    FROM dbo.[Task]
    WHERE [TenantId] = @TenantId
      AND (@Status IS NULL OR [Status] = @Status)
      AND (@SourceType IS NULL OR [SourceType] = @SourceType)
      AND (@SourceId IS NULL OR [SourceId] = @SourceId)
      AND (@OpenOnly = 0 OR [Status] NOT IN ('completed', 'cancelled'))
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS UpdateTask;
GO

CREATE PROCEDURE UpdateTask
(
    @PublicId UNIQUEIDENTIFIER,
    @Title NVARCHAR(500) = NULL,
    @Status NVARCHAR(50) = NULL,
    @Description NVARCHAR(MAX) = NULL,
    @Context NVARCHAR(MAX) = NULL,
    @BillId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Task]
    SET
        [Title] = COALESCE(@Title, [Title]),
        [Status] = COALESCE(@Status, [Status]),
        [Description] = COALESCE(@Description, [Description]),
        [Context] = COALESCE(@Context, [Context]),
        [BillId] = CASE WHEN @BillId IS NOT NULL THEN @BillId ELSE [BillId] END,
        [ModifiedDatetime] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[TaskType],
        INSERTED.[ReferenceId],
        INSERTED.[Title],
        INSERTED.[Status],
        INSERTED.[SourceType],
        INSERTED.[SourceId],
        INSERTED.[Description],
        INSERTED.[CreatedByUserId],
        INSERTED.[WorkflowId],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[BillId],
        INSERTED.[Context]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadTaskByWorkflowId;
GO

CREATE PROCEDURE ReadTaskByWorkflowId
(
    @WorkflowId INT
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
        [TaskType],
        [ReferenceId],
        [Title],
        [Status],
        [SourceType],
        [SourceId],
        [Description],
        [CreatedByUserId],
        [WorkflowId],
        [VendorId],
        [ProjectId],
        [BillId],
        [Context]
    FROM dbo.[Task]
    WHERE [WorkflowId] = @WorkflowId;

    COMMIT TRANSACTION;
END;
GO
