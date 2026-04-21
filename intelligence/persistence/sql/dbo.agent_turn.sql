-- ============================================================================
-- AgentTurn — Table
-- ----------------------------------------------------------------------------
-- One row per LLM call within an AgentSession. Written at turn start,
-- updated on turn end with usage, stop_reason, and assistant text.
-- ============================================================================

IF OBJECT_ID('dbo.AgentTurn', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AgentTurn
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [SessionId] BIGINT NOT NULL,
        [TurnNumber] INT NOT NULL,
        [Model] NVARCHAR(100) NOT NULL,
        [InputTokens] INT NOT NULL DEFAULT 0,
        [OutputTokens] INT NOT NULL DEFAULT 0,
        [StopReason] NVARCHAR(50) NULL,
        [AssistantText] NVARCHAR(MAX) NULL,
        [StartedAt] DATETIME2(3) NOT NULL,
        [CompletedAt] DATETIME2(3) NULL,
        CONSTRAINT [FK_AgentTurn_SessionId]
            FOREIGN KEY ([SessionId]) REFERENCES dbo.[AgentSession] ([Id])
    );
END;
GO

-- Indexes
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentTurn_SessionId' AND object_id = OBJECT_ID('dbo.AgentTurn'))
BEGIN
    CREATE INDEX [IX_AgentTurn_SessionId] ON [dbo].[AgentTurn] ([SessionId], [TurnNumber]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentTurn_PublicId' AND object_id = OBJECT_ID('dbo.AgentTurn'))
BEGIN
    CREATE INDEX [IX_AgentTurn_PublicId] ON [dbo].[AgentTurn] ([PublicId]);
END
GO


-- ============================================================================
-- AgentTurn — View
-- ============================================================================

CREATE OR ALTER VIEW [dbo].[vw_AgentTurn]
AS
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [SessionId],
        [TurnNumber],
        [Model],
        [InputTokens],
        [OutputTokens],
        [StopReason],
        [AssistantText],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt]
    FROM dbo.[AgentTurn];
GO


-- ============================================================================
-- AgentTurn — Stored Procedures
-- ============================================================================

CREATE OR ALTER PROCEDURE CreateAgentTurn
(
    @SessionId BIGINT,
    @TurnNumber INT,
    @Model NVARCHAR(100)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[AgentTurn]
        ([CreatedDatetime], [ModifiedDatetime], [SessionId], [TurnNumber],
         [Model], [StartedAt])
    VALUES
        (@Now, @Now, @SessionId, @TurnNumber,
         @Model, @Now);

    SELECT * FROM dbo.[vw_AgentTurn] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE CompleteAgentTurn
(
    @Id BIGINT,
    @InputTokens INT,
    @OutputTokens INT,
    @StopReason NVARCHAR(50) = NULL,
    @AssistantText NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[AgentTurn]
    SET
        [ModifiedDatetime] = @Now,
        [InputTokens] = @InputTokens,
        [OutputTokens] = @OutputTokens,
        [StopReason] = @StopReason,
        [AssistantText] = @AssistantText,
        [CompletedAt] = @Now
    WHERE [Id] = @Id;

    IF @@ROWCOUNT > 0
        SELECT * FROM dbo.[vw_AgentTurn] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentTurnById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentTurn] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentTurnsBySessionId
(
    @SessionId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentTurn]
    WHERE [SessionId] = @SessionId
    ORDER BY [TurnNumber] ASC;
END;
GO
