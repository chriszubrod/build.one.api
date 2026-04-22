-- ============================================================================
-- AgentSession — Table
-- ----------------------------------------------------------------------------
-- One row per agent run. Written at start (Status='running') and updated as
-- the run progresses. Finalized to 'completed' or 'failed' at end.
--
-- ParentSessionId (nullable) supports future hierarchical multi-agent flows:
-- when a specialist agent is invoked as a tool by a parent agent, the
-- specialist's session FKs back to the parent's session for audit-trail
-- nesting.
-- ============================================================================

IF OBJECT_ID('dbo.AgentSession', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AgentSession
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [AgentName] NVARCHAR(100) NOT NULL,
        [AgentUserId] BIGINT NULL,
        [RequestingUserId] BIGINT NULL,
        [ParentSessionId] BIGINT NULL,
        [PreviousSessionId] BIGINT NULL,
        [Model] NVARCHAR(100) NOT NULL,
        [Provider] NVARCHAR(50) NOT NULL,
        [UserMessage] NVARCHAR(MAX) NOT NULL,
        [SystemPrompt] NVARCHAR(MAX) NULL,
        [Status] NVARCHAR(20) NOT NULL,
        [TerminationReason] NVARCHAR(50) NULL,
        [TotalInputTokens] INT NOT NULL DEFAULT 0,
        [TotalOutputTokens] INT NOT NULL DEFAULT 0,
        [StartedAt] DATETIME2(3) NOT NULL,
        [CompletedAt] DATETIME2(3) NULL,
        [ErrorMessage] NVARCHAR(MAX) NULL
    );
END;
GO

-- Idempotent column add for existing environments
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = 'ParentSessionId' AND Object_ID = OBJECT_ID('dbo.AgentSession')
)
BEGIN
    ALTER TABLE dbo.[AgentSession] ADD [ParentSessionId] BIGINT NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = 'PreviousSessionId' AND Object_ID = OBJECT_ID('dbo.AgentSession')
)
BEGIN
    ALTER TABLE dbo.[AgentSession] ADD [PreviousSessionId] BIGINT NULL;
END
GO

-- Self-referential FK constraint (parent — for sub-agent composition)
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AgentSession_ParentSessionId'
)
BEGIN
    ALTER TABLE dbo.[AgentSession] WITH CHECK
        ADD CONSTRAINT [FK_AgentSession_ParentSessionId]
            FOREIGN KEY ([ParentSessionId]) REFERENCES dbo.[AgentSession] ([Id]);
END
GO

-- Self-referential FK constraint (previous — for conversation threading)
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AgentSession_PreviousSessionId'
)
BEGIN
    ALTER TABLE dbo.[AgentSession] WITH CHECK
        ADD CONSTRAINT [FK_AgentSession_PreviousSessionId]
            FOREIGN KEY ([PreviousSessionId]) REFERENCES dbo.[AgentSession] ([Id]);
END
GO

-- Indexes
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentSession_PublicId' AND object_id = OBJECT_ID('dbo.AgentSession'))
BEGIN
    CREATE INDEX [IX_AgentSession_PublicId] ON [dbo].[AgentSession] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentSession_Status' AND object_id = OBJECT_ID('dbo.AgentSession'))
BEGIN
    CREATE INDEX [IX_AgentSession_Status] ON [dbo].[AgentSession] ([Status]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentSession_StartedAt' AND object_id = OBJECT_ID('dbo.AgentSession'))
BEGIN
    CREATE INDEX [IX_AgentSession_StartedAt] ON [dbo].[AgentSession] ([StartedAt] DESC);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentSession_ParentSessionId' AND object_id = OBJECT_ID('dbo.AgentSession'))
BEGIN
    CREATE INDEX [IX_AgentSession_ParentSessionId] ON [dbo].[AgentSession] ([ParentSessionId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentSession_PreviousSessionId' AND object_id = OBJECT_ID('dbo.AgentSession'))
BEGIN
    CREATE INDEX [IX_AgentSession_PreviousSessionId] ON [dbo].[AgentSession] ([PreviousSessionId]);
END
GO


-- ============================================================================
-- AgentSession — View (single source of truth for column formatting)
-- ============================================================================

CREATE OR ALTER VIEW [dbo].[vw_AgentSession]
AS
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [AgentName],
        [AgentUserId],
        [RequestingUserId],
        [ParentSessionId],
        [PreviousSessionId],
        [Model],
        [Provider],
        [UserMessage],
        [SystemPrompt],
        [Status],
        [TerminationReason],
        [TotalInputTokens],
        [TotalOutputTokens],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        [ErrorMessage]
    FROM dbo.[AgentSession];
GO


-- ============================================================================
-- AgentSession — Stored Procedures
-- ============================================================================

CREATE OR ALTER PROCEDURE CreateAgentSession
(
    @AgentName NVARCHAR(100),
    @AgentUserId BIGINT = NULL,
    @RequestingUserId BIGINT = NULL,
    @ParentSessionId BIGINT = NULL,
    @PreviousSessionId BIGINT = NULL,
    @Model NVARCHAR(100),
    @Provider NVARCHAR(50),
    @UserMessage NVARCHAR(MAX),
    @SystemPrompt NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[AgentSession]
        ([CreatedDatetime], [ModifiedDatetime], [AgentName], [AgentUserId],
         [RequestingUserId], [ParentSessionId], [PreviousSessionId],
         [Model], [Provider], [UserMessage], [SystemPrompt], [Status], [StartedAt])
    VALUES
        (@Now, @Now, @AgentName, @AgentUserId,
         @RequestingUserId, @ParentSessionId, @PreviousSessionId,
         @Model, @Provider, @UserMessage, @SystemPrompt, 'running', @Now);

    SELECT * FROM dbo.[vw_AgentSession] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE CompleteAgentSession
(
    @Id BIGINT,
    @TerminationReason NVARCHAR(50),
    @TotalInputTokens INT,
    @TotalOutputTokens INT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[AgentSession]
    SET
        [ModifiedDatetime] = @Now,
        [Status] = 'completed',
        [TerminationReason] = @TerminationReason,
        [TotalInputTokens] = @TotalInputTokens,
        [TotalOutputTokens] = @TotalOutputTokens,
        [CompletedAt] = @Now
    WHERE [Id] = @Id;

    IF @@ROWCOUNT > 0
        SELECT * FROM dbo.[vw_AgentSession] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE FailAgentSession
(
    @Id BIGINT,
    @ErrorMessage NVARCHAR(MAX),
    @TotalInputTokens INT = 0,
    @TotalOutputTokens INT = 0
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[AgentSession]
    SET
        [ModifiedDatetime] = @Now,
        [Status] = 'failed',
        [ErrorMessage] = @ErrorMessage,
        [TotalInputTokens] = @TotalInputTokens,
        [TotalOutputTokens] = @TotalOutputTokens,
        [CompletedAt] = @Now
    WHERE [Id] = @Id;

    IF @@ROWCOUNT > 0
        SELECT * FROM dbo.[vw_AgentSession] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentSessionById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentSession] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentSessionByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentSession] WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadRecentAgentSessions
(
    @Top INT = 50
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (@Top) * FROM dbo.[vw_AgentSession] ORDER BY [StartedAt] DESC;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentSessionsByParentId
(
    @ParentSessionId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentSession]
    WHERE [ParentSessionId] = @ParentSessionId
    ORDER BY [StartedAt] ASC;
END;
GO
