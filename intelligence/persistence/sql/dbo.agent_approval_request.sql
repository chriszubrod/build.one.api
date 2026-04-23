-- ============================================================================
-- AgentApprovalRequest — Table
-- ----------------------------------------------------------------------------
-- One row per agent-initiated action that required user approval. Written
-- when the loop pauses on a requires_approval tool (Status='pending').
-- Updated with the user's decision (approved / rejected / timed_out) plus
-- the FinalInput that was actually executed (may differ from ProposedInput
-- when the user edited the values before approving).
-- ============================================================================

IF OBJECT_ID('dbo.AgentApprovalRequest', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AgentApprovalRequest
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL,
        [ModifiedDatetime] DATETIME2(3) NULL,
        [SessionId] BIGINT NOT NULL,
        [TurnId] BIGINT NULL,
        [RequestId] NVARCHAR(100) NOT NULL,
        [ToolName] NVARCHAR(100) NOT NULL,
        [Summary] NVARCHAR(500) NULL,
        [ProposedInput] NVARCHAR(MAX) NOT NULL,
        [Status] NVARCHAR(20) NOT NULL,
        [FinalInput] NVARCHAR(MAX) NULL,
        [DecidedByUserId] BIGINT NULL,
        [DecidedAt] DATETIME2(3) NULL,
        CONSTRAINT [FK_AgentApprovalRequest_SessionId]
            FOREIGN KEY ([SessionId]) REFERENCES dbo.[AgentSession] ([Id]),
        CONSTRAINT [FK_AgentApprovalRequest_TurnId]
            FOREIGN KEY ([TurnId]) REFERENCES dbo.[AgentTurn] ([Id])
    );
END;
GO

-- Indexes
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentApprovalRequest_PublicId' AND object_id = OBJECT_ID('dbo.AgentApprovalRequest'))
BEGIN
    CREATE INDEX [IX_AgentApprovalRequest_PublicId] ON [dbo].[AgentApprovalRequest] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentApprovalRequest_SessionId' AND object_id = OBJECT_ID('dbo.AgentApprovalRequest'))
BEGIN
    CREATE INDEX [IX_AgentApprovalRequest_SessionId] ON [dbo].[AgentApprovalRequest] ([SessionId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentApprovalRequest_Status' AND object_id = OBJECT_ID('dbo.AgentApprovalRequest'))
BEGIN
    CREATE INDEX [IX_AgentApprovalRequest_Status] ON [dbo].[AgentApprovalRequest] ([Status]);
END
GO

-- Compound index for the lookup the approve endpoint needs:
-- (SessionId, RequestId) — unique in practice though we don't enforce it
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AgentApprovalRequest_SessionId_RequestId' AND object_id = OBJECT_ID('dbo.AgentApprovalRequest'))
BEGIN
    CREATE INDEX [IX_AgentApprovalRequest_SessionId_RequestId]
        ON [dbo].[AgentApprovalRequest] ([SessionId], [RequestId]);
END
GO


-- ============================================================================
-- AgentApprovalRequest — View
-- ============================================================================

CREATE OR ALTER VIEW [dbo].[vw_AgentApprovalRequest]
AS
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [SessionId],
        [TurnId],
        [RequestId],
        [ToolName],
        [Summary],
        [ProposedInput],
        [Status],
        [FinalInput],
        [DecidedByUserId],
        CONVERT(VARCHAR(19), [DecidedAt], 120) AS [DecidedAt]
    FROM dbo.[AgentApprovalRequest];
GO


-- ============================================================================
-- AgentApprovalRequest — Stored Procedures
-- ============================================================================

CREATE OR ALTER PROCEDURE CreateAgentApprovalRequest
(
    @SessionId BIGINT,
    @TurnId BIGINT = NULL,
    @RequestId NVARCHAR(100),
    @ToolName NVARCHAR(100),
    @Summary NVARCHAR(500) = NULL,
    @ProposedInput NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[AgentApprovalRequest]
        ([CreatedDatetime], [ModifiedDatetime], [SessionId], [TurnId],
         [RequestId], [ToolName], [Summary], [ProposedInput], [Status])
    VALUES
        (@Now, @Now, @SessionId, @TurnId,
         @RequestId, @ToolName, @Summary, @ProposedInput, 'pending');

    SELECT * FROM dbo.[vw_AgentApprovalRequest] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE SetAgentApprovalRequestDecision
(
    @Id BIGINT,
    @Status NVARCHAR(20),             -- approved | rejected | timed_out
    @FinalInput NVARCHAR(MAX) = NULL,
    @DecidedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[AgentApprovalRequest]
    SET
        [ModifiedDatetime] = @Now,
        [Status] = @Status,
        [FinalInput] = @FinalInput,
        [DecidedByUserId] = @DecidedByUserId,
        [DecidedAt] = @Now
    WHERE [Id] = @Id;

    IF @@ROWCOUNT > 0
        SELECT * FROM dbo.[vw_AgentApprovalRequest] WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentApprovalRequestById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentApprovalRequest] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentApprovalRequestByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentApprovalRequest] WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentApprovalRequestBySessionRequest
(
    @SessionId BIGINT,
    @RequestId NVARCHAR(100)
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_AgentApprovalRequest]
    WHERE [SessionId] = @SessionId AND [RequestId] = @RequestId
    ORDER BY [Id] DESC;  -- newest in case of retries
END;
GO


CREATE OR ALTER PROCEDURE ReadAgentApprovalRequestsBySessionId
(
    @SessionId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_AgentApprovalRequest]
    WHERE [SessionId] = @SessionId
    ORDER BY [CreatedDatetime] ASC;
END;
GO
