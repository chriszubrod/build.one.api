-- ============================================================================
-- AgentToolCall — Table
-- ----------------------------------------------------------------------------
-- One row per tool invocation within an AgentTurn. Written at ToolCallStart,
-- updated at ToolCallEnd with output and is_error.
-- ============================================================================

IF OBJECT_ID('dbo.AgentToolCall', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AgentToolCall
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [TurnId] BIGINT NOT NULL,
        [ToolUseId] NVARCHAR(100) NOT NULL,
        [ToolName] NVARCHAR(100) NOT NULL,
        [ToolInput] NVARCHAR(MAX) NOT NULL,
        [ToolOutput] NVARCHAR(MAX) NULL,
        [IsError] BIT NOT NULL DEFAULT 0,
        [StartedAt] DATETIME2(3) NOT NULL,
        [CompletedAt] DATETIME2(3) NULL,
        CONSTRAINT [FK_AgentToolCall_TurnId]
            FOREIGN KEY ([TurnId]) REFERENCES dbo.[AgentTurn] ([Id])
    );
END;
GO

-- Indexes
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentToolCall_TurnId' AND object_id = OBJECT_ID('dbo.AgentToolCall'))
BEGIN
    CREATE INDEX [IX_AgentToolCall_TurnId] ON [dbo].[AgentToolCall] ([TurnId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentToolCall_PublicId' AND object_id = OBJECT_ID('dbo.AgentToolCall'))
BEGIN
    CREATE INDEX [IX_AgentToolCall_PublicId] ON [dbo].[AgentToolCall] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentToolCall_ToolUseId' AND object_id = OBJECT_ID('dbo.AgentToolCall'))
BEGIN
    CREATE INDEX [IX_AgentToolCall_ToolUseId] ON [dbo].[AgentToolCall] ([ToolUseId]);
END
GO


-- ============================================================================
-- AgentToolCall — View
-- ============================================================================

CREATE OR ALTER VIEW [dbo].[vw_AgentToolCall]
AS
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [TurnId],
        [ToolUseId],
        [ToolName],
        [ToolInput],
        [ToolOutput],
        [IsError],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt]
    FROM dbo.[AgentToolCall];
GO


-- ============================================================================
-- AgentToolCall — Stored Procedures
-- ============================================================================

CREATE OR ALTER PROCEDURE CreateAgentToolCall
(
    @TurnId BIGINT,
    @ToolUseId NVARCHAR(100),
    @ToolName NVARCHAR(100),
    @ToolInput NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[AgentToolCall]
        ([CreatedDatetime], [ModifiedDatetime], [TurnId], [ToolUseId],
         [ToolName], [ToolInput], [StartedAt])
    VALUES
        (@Now, @Now, @TurnId, @ToolUseId,
         @ToolName, @ToolInput, @Now);

    SELECT * FROM dbo.[vw_AgentToolCall] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE CompleteAgentToolCall
(
    @Id BIGINT,
    @ToolOutput NVARCHAR(MAX),
    @IsError BIT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[AgentToolCall]
    SET
        [ModifiedDatetime] = @Now,
        [ToolOutput] = @ToolOutput,
        [IsError] = @IsError,
        [CompletedAt] = @Now
    WHERE [Id] = @Id;

    IF @@ROWCOUNT > 0
        SELECT * FROM dbo.[vw_AgentToolCall] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentToolCallById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentToolCall] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentToolCallsByTurnId
(
    @TurnId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentToolCall]
    WHERE [TurnId] = @TurnId
    ORDER BY [StartedAt] ASC;
END;
GO
