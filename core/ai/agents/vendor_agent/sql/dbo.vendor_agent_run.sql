-- =============================================================================
-- VendorAgent Tables with Stored Procedures (dbo schema)
-- =============================================================================
--
-- This schema supports the VendorAgent system:
-- - VendorAgentRun: Tracks each execution of the agent
--
-- =============================================================================


-- =============================================================================
-- VendorAgentRun: Tracks each execution/invocation of the VendorAgent
-- =============================================================================

IF OBJECT_ID('dbo.VendorAgentRun', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorAgentRun]
(
    -- Standard columns
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Tenant isolation
    [TenantId] BIGINT NOT NULL,

    -- Run identification
    [AgentType] VARCHAR(50) NOT NULL DEFAULT 'vendor_agent',
    [TriggerType] VARCHAR(50) NOT NULL,           -- 'scheduled', 'event', 'manual'
    [TriggerSource] VARCHAR(100) NULL,            -- e.g., 'vendor_created', 'daily_review', 'user_request'

    -- Run state
    [Status] VARCHAR(20) NOT NULL DEFAULT 'running',  -- 'running', 'completed', 'failed', 'cancelled'
    [StartedDatetime] DATETIME2(3) NOT NULL,
    [CompletedDatetime] DATETIME2(3) NULL,

    -- Metrics
    [VendorsProcessed] INT NOT NULL DEFAULT 0,
    [ProposalsCreated] INT NOT NULL DEFAULT 0,
    [ErrorCount] INT NOT NULL DEFAULT 0,

    -- Context and results
    [Context] NVARCHAR(MAX) NULL,                 -- JSON: input parameters, configuration
    [Summary] NVARCHAR(MAX) NULL,                 -- JSON: run summary, stats, errors

    -- Audit
    [CreatedBy] VARCHAR(200) NULL,

    CONSTRAINT [UQ_VendorAgentRun_PublicId] UNIQUE ([PublicId])
);
END
GO

CREATE INDEX IX_VendorAgentRun_TenantStatus ON [dbo].[VendorAgentRun]([TenantId], [Status]);
CREATE INDEX IX_VendorAgentRun_CreatedDatetime ON [dbo].[VendorAgentRun]([CreatedDatetime] DESC);
GO


-- =============================================================================
-- Stored Procedures: VendorAgentRun
-- =============================================================================

CREATE OR ALTER PROCEDURE CreateVendorAgentRun
(
    @TenantId BIGINT,
    @AgentType VARCHAR(50) = 'vendor_agent',
    @TriggerType VARCHAR(50),
    @TriggerSource VARCHAR(100) = NULL,
    @Context NVARCHAR(MAX) = NULL,
    @CreatedBy VARCHAR(200) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorAgentRun] (
        [CreatedDatetime], [ModifiedDatetime], [TenantId], [AgentType],
        [TriggerType], [TriggerSource], [Status], [StartedDatetime],
        [Context], [CreatedBy]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[AgentType],
        INSERTED.[TriggerType],
        INSERTED.[TriggerSource],
        INSERTED.[Status],
        CONVERT(VARCHAR(19), INSERTED.[StartedDatetime], 120) AS [StartedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime],
        INSERTED.[VendorsProcessed],
        INSERTED.[ProposalsCreated],
        INSERTED.[ErrorCount],
        INSERTED.[Context],
        INSERTED.[Summary],
        INSERTED.[CreatedBy]
    VALUES (
        @Now, @Now, @TenantId, @AgentType,
        @TriggerType, @TriggerSource, 'running', @Now,
        @Context, @CreatedBy
    );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateVendorAgentRunStatus
(
    @PublicId UNIQUEIDENTIFIER,
    @Status VARCHAR(20),
    @VendorsProcessed INT = NULL,
    @ProposalsCreated INT = NULL,
    @ErrorCount INT = NULL,
    @Summary NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorAgentRun]
    SET
        [Status] = @Status,
        [ModifiedDatetime] = @Now,
        [CompletedDatetime] = CASE WHEN @Status IN ('completed', 'failed', 'cancelled') THEN @Now ELSE [CompletedDatetime] END,
        [VendorsProcessed] = COALESCE(@VendorsProcessed, [VendorsProcessed]),
        [ProposalsCreated] = COALESCE(@ProposalsCreated, [ProposalsCreated]),
        [ErrorCount] = COALESCE(@ErrorCount, [ErrorCount]),
        [Summary] = COALESCE(@Summary, [Summary])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[AgentType],
        INSERTED.[TriggerType],
        INSERTED.[TriggerSource],
        INSERTED.[Status],
        CONVERT(VARCHAR(19), INSERTED.[StartedDatetime], 120) AS [StartedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[CompletedDatetime], 120) AS [CompletedDatetime],
        INSERTED.[VendorsProcessed],
        INSERTED.[ProposalsCreated],
        INSERTED.[ErrorCount],
        INSERTED.[Context],
        INSERTED.[Summary],
        INSERTED.[CreatedBy]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorAgentRunByPublicId
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
        [AgentType],
        [TriggerType],
        [TriggerSource],
        [Status],
        CONVERT(VARCHAR(19), [StartedDatetime], 120) AS [StartedDatetime],
        CONVERT(VARCHAR(19), [CompletedDatetime], 120) AS [CompletedDatetime],
        [VendorsProcessed],
        [ProposalsCreated],
        [ErrorCount],
        [Context],
        [Summary],
        [CreatedBy]
    FROM dbo.[VendorAgentRun]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorAgentRunsByTenant
(
    @TenantId BIGINT,
    @Status VARCHAR(20) = NULL,
    @Limit INT = 50
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF @Status IS NULL
        SELECT TOP (@Limit)
            [Id],
            [PublicId],
            [RowVersion],
            CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
            [TenantId],
            [AgentType],
            [TriggerType],
            [TriggerSource],
            [Status],
            CONVERT(VARCHAR(19), [StartedDatetime], 120) AS [StartedDatetime],
            CONVERT(VARCHAR(19), [CompletedDatetime], 120) AS [CompletedDatetime],
            [VendorsProcessed],
            [ProposalsCreated],
            [ErrorCount],
            [Context],
            [Summary],
            [CreatedBy]
        FROM dbo.[VendorAgentRun]
        WHERE [TenantId] = @TenantId
        ORDER BY [CreatedDatetime] DESC;
    ELSE
        SELECT TOP (@Limit)
            [Id],
            [PublicId],
            [RowVersion],
            CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
            [TenantId],
            [AgentType],
            [TriggerType],
            [TriggerSource],
            [Status],
            CONVERT(VARCHAR(19), [StartedDatetime], 120) AS [StartedDatetime],
            CONVERT(VARCHAR(19), [CompletedDatetime], 120) AS [CompletedDatetime],
            [VendorsProcessed],
            [ProposalsCreated],
            [ErrorCount],
            [Context],
            [Summary],
            [CreatedBy]
        FROM dbo.[VendorAgentRun]
        WHERE [TenantId] = @TenantId AND [Status] = @Status
        ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


