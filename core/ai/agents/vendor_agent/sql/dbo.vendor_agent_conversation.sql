-- =============================================================================
-- VendorAgent Tables with Stored Procedures (dbo schema)
-- =============================================================================
--
-- This schema supports the VendorAgent system:
-- - VendorAgentConversation: Vendor-scoped conversation history for context continuity
--
-- =============================================================================


-- =============================================================================
-- VendorAgentConversation: Vendor-scoped conversation history
-- =============================================================================

IF OBJECT_ID('dbo.VendorAgentConversation', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorAgentConversation]
(
    -- Standard columns
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Tenant isolation
    [TenantId] BIGINT NOT NULL,

    -- Scoping
    [VendorId] BIGINT NOT NULL,                   -- FK to Vendor (conversation is vendor-scoped)

    -- Message details
    [Role] VARCHAR(20) NOT NULL,                  -- 'system', 'agent', 'user', 'tool'
    [Content] NVARCHAR(MAX) NOT NULL,             -- Message content
    [MessageType] VARCHAR(50) NULL,               -- 'reasoning', 'proposal', 'approval', 'rejection', 'clarification', 'tool_call', 'tool_result'

    -- Relationships
    [AgentRunId] BIGINT NULL,                     -- Which agent run generated this (NULL for user messages)
    [ProposalId] BIGINT NULL,                     -- Related proposal (if applicable)

    -- Metadata
    [Metadata] NVARCHAR(MAX) NULL,                -- JSON: tool calls, timestamps, model info

    CONSTRAINT [UQ_VendorAgentConversation_PublicId] UNIQUE ([PublicId]),
    CONSTRAINT [FK_VendorAgentConversation_AgentRun] FOREIGN KEY ([AgentRunId])
        REFERENCES [dbo].[VendorAgentRun]([Id])
);
END
GO

CREATE INDEX IX_VendorAgentConversation_VendorId ON [dbo].[VendorAgentConversation]([VendorId], [CreatedDatetime]);
CREATE INDEX IX_VendorAgentConversation_TenantId ON [dbo].[VendorAgentConversation]([TenantId]);
GO



-- =============================================================================
-- Stored Procedures: VendorAgentConversation
-- =============================================================================

CREATE OR ALTER PROCEDURE CreateVendorAgentConversationMessage
(
    @TenantId BIGINT,
    @VendorId BIGINT,
    @Role VARCHAR(20),
    @Content NVARCHAR(MAX),
    @MessageType VARCHAR(50) = NULL,
    @AgentRunId BIGINT = NULL,
    @ProposalId BIGINT = NULL,
    @Metadata NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorAgentConversation] (
        [CreatedDatetime], [ModifiedDatetime], [TenantId], [VendorId],
        [Role], [Content], [MessageType], [AgentRunId], [ProposalId], [Metadata]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[VendorId],
        INSERTED.[Role],
        INSERTED.[Content],
        INSERTED.[MessageType],
        INSERTED.[AgentRunId],
        INSERTED.[ProposalId],
        INSERTED.[Metadata]
    VALUES (
        @Now, @Now, @TenantId, @VendorId,
        @Role, @Content, @MessageType, @AgentRunId, @ProposalId, @Metadata
    );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorAgentConversation
(
    @VendorId BIGINT,
    @Limit INT = 100
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
        [TenantId],
        [VendorId],
        [Role],
        [Content],
        [MessageType],
        [AgentRunId],
        [ProposalId],
        [Metadata]
    FROM dbo.[VendorAgentConversation]
    WHERE [VendorId] = @VendorId
    ORDER BY [CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO


-- Read recent conversation for context window (most recent N messages)
CREATE OR ALTER PROCEDURE ReadRecentVendorAgentConversation
(
    @VendorId BIGINT,
    @Limit INT = 20
)
AS
BEGIN
    BEGIN TRANSACTION;

    ;WITH RecentMessages AS (
        SELECT TOP (@Limit)
            [Id],
            [PublicId],
            [RowVersion],
            CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
            [TenantId],
            [VendorId],
            [Role],
            [Content],
            [MessageType],
            [AgentRunId],
            [ProposalId],
            [Metadata]
        FROM dbo.[VendorAgentConversation]
        WHERE [VendorId] = @VendorId
        ORDER BY [CreatedDatetime] DESC
    )
    SELECT * FROM RecentMessages ORDER BY [CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO


-- Clear conversation history for a vendor (for "fresh start" sessions)
CREATE OR ALTER PROCEDURE ClearVendorAgentConversation
(
    @VendorId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[VendorAgentConversation]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO
