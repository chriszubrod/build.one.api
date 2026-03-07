-- =============================================================================
-- BillAgentRun Table with Stored Procedures (dbo schema)
-- =============================================================================
--
-- Tracks each execution of the BillAgent folder processing service.
--
-- =============================================================================


-- =============================================================================
-- BillAgentRun: Tracks each execution of the BillAgent
-- =============================================================================

IF OBJECT_ID('dbo.BillAgentRun', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillAgentRun]
(
    -- Standard columns
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Run state
    [Status] VARCHAR(20) NOT NULL DEFAULT 'running',  -- 'running', 'completed', 'failed'
    [TriggerSource] VARCHAR(50) NULL,                  -- 'manual', 'scheduler'
    [CompletedDatetime] DATETIME2(3) NULL,

    -- Metrics
    [FilesFound] INT NOT NULL DEFAULT 0,
    [FilesProcessed] INT NOT NULL DEFAULT 0,
    [FilesSkipped] INT NOT NULL DEFAULT 0,
    [BillsCreated] INT NOT NULL DEFAULT 0,
    [ErrorCount] INT NOT NULL DEFAULT 0,

    -- Results
    [Summary] NVARCHAR(MAX) NULL,

    -- Audit
    [CreatedBy] VARCHAR(200) NULL,

    CONSTRAINT [UQ_BillAgentRun_PublicId] UNIQUE ([PublicId])
);
END
GO

CREATE INDEX IX_BillAgentRun_Status ON [dbo].[BillAgentRun]([Status]);
CREATE INDEX IX_BillAgentRun_CreatedDatetime ON [dbo].[BillAgentRun]([CreatedDatetime] DESC);
GO


-- =============================================================================
-- Stored Procedures: BillAgentRun
-- =============================================================================

CREATE OR ALTER PROCEDURE CreateBillAgentRun
(
    @TriggerSource VARCHAR(50) = NULL,
    @CreatedBy VARCHAR(200) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillAgentRun] (
        [CreatedDatetime], [ModifiedDatetime], [TriggerSource], [Status], [CreatedBy]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Status],
        INSERTED.[TriggerSource],
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime],
        INSERTED.[FilesFound],
        INSERTED.[FilesProcessed],
        INSERTED.[FilesSkipped],
        INSERTED.[BillsCreated],
        INSERTED.[ErrorCount],
        INSERTED.[Summary],
        INSERTED.[CreatedBy]
    VALUES (
        @Now, @Now, @TriggerSource, 'running', @CreatedBy
    );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE CompleteBillAgentRun
(
    @PublicId UNIQUEIDENTIFIER,
    @FilesFound INT = 0,
    @FilesProcessed INT = 0,
    @FilesSkipped INT = 0,
    @BillsCreated INT = 0,
    @ErrorCount INT = 0,
    @Summary NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillAgentRun]
    SET
        [Status] = 'completed',
        [ModifiedDatetime] = @Now,
        [CompletedDatetime] = @Now,
        [FilesFound] = @FilesFound,
        [FilesProcessed] = @FilesProcessed,
        [FilesSkipped] = @FilesSkipped,
        [BillsCreated] = @BillsCreated,
        [ErrorCount] = @ErrorCount,
        [Summary] = @Summary
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Status],
        INSERTED.[TriggerSource],
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime],
        INSERTED.[FilesFound],
        INSERTED.[FilesProcessed],
        INSERTED.[FilesSkipped],
        INSERTED.[BillsCreated],
        INSERTED.[ErrorCount],
        INSERTED.[Summary],
        INSERTED.[CreatedBy]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE FailBillAgentRun
(
    @PublicId UNIQUEIDENTIFIER,
    @Summary NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillAgentRun]
    SET
        [Status] = 'failed',
        [ModifiedDatetime] = @Now,
        [CompletedDatetime] = @Now,
        [Summary] = @Summary
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Status],
        INSERTED.[TriggerSource],
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime],
        INSERTED.[FilesFound],
        INSERTED.[FilesProcessed],
        INSERTED.[FilesSkipped],
        INSERTED.[BillsCreated],
        INSERTED.[ErrorCount],
        INSERTED.[Summary],
        INSERTED.[CreatedBy]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadBillAgentRunByPublicId
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
        [Status],
        [TriggerSource],
        CONVERT(VARCHAR(19), [CompletedDatetime], 120) AS [CompletedDatetime],
        [FilesFound],
        [FilesProcessed],
        [FilesSkipped],
        [BillsCreated],
        [ErrorCount],
        [Summary],
        [CreatedBy]
    FROM dbo.[BillAgentRun]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateBillAgentRunProgress
(
    @PublicId UNIQUEIDENTIFIER,
    @FilesFound INT = 0,
    @FilesProcessed INT = 0,
    @FilesSkipped INT = 0,
    @BillsCreated INT = 0,
    @ErrorCount INT = 0
)
AS
BEGIN
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillAgentRun]
    SET
        [ModifiedDatetime] = @Now,
        [FilesFound] = @FilesFound,
        [FilesProcessed] = @FilesProcessed,
        [FilesSkipped] = @FilesSkipped,
        [BillsCreated] = @BillsCreated,
        [ErrorCount] = @ErrorCount
    WHERE [PublicId] = @PublicId
      AND [Status] = 'running';
END;
GO


CREATE OR ALTER PROCEDURE ReadRecentBillAgentRuns
(
    @Limit INT = 20
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP (@Limit)
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Status],
        [TriggerSource],
        CONVERT(VARCHAR(19), [CompletedDatetime], 120) AS [CompletedDatetime],
        [FilesFound],
        [FilesProcessed],
        [FilesSkipped],
        [BillsCreated],
        [ErrorCount],
        [Summary],
        [CreatedBy]
    FROM dbo.[BillAgentRun]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO
