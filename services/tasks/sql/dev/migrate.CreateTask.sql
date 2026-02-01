-- Add missing columns to dbo.Task (if your table was created from an older schema),
-- then update CreateTask procedure to match current code.
-- Run this if you see "Invalid column name" or "CreateTask has too many arguments".

-- =============================================================================
-- 1. Add missing columns to dbo.Task (safe: only add if column does not exist)
-- =============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'Description')
    ALTER TABLE dbo.[Task] ADD [Description] NVARCHAR(MAX) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'CreatedByUserId')
    ALTER TABLE dbo.[Task] ADD [CreatedByUserId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'WorkflowId')
    ALTER TABLE dbo.[Task] ADD [WorkflowId] INT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'VendorId')
    ALTER TABLE dbo.[Task] ADD [VendorId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'ProjectId')
    ALTER TABLE dbo.[Task] ADD [ProjectId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'BillId')
    ALTER TABLE dbo.[Task] ADD [BillId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'Context')
    ALTER TABLE dbo.[Task] ADD [Context] NVARCHAR(MAX) NULL;
GO

-- Optional: index on WorkflowId if we just added it (skip if index already exists)
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'WorkflowId')
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('dbo.Task') AND name = 'IX_Task_WorkflowId')
    CREATE INDEX IX_Task_WorkflowId ON dbo.[Task]([WorkflowId]);
GO

-- =============================================================================
-- 2. Update CreateTask procedure (14 parameters)
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

-- =============================================================================
-- 3. Update UpdateTask procedure to support BillId
-- =============================================================================

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
