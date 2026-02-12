-- =============================================================================
-- VendorAgent Tables with Stored Procedures (dbo schema)
-- =============================================================================
--
-- This schema supports the VendorAgent system:
-- - VendorAgentProposal: Vendor-level proposals (can have multiple field changes)
--
-- =============================================================================


-- =============================================================================
-- VendorAgentProposal: Vendor-level proposals (one or more field changes)
-- =============================================================================

IF OBJECT_ID('dbo.VendorAgentProposal', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorAgentProposal]
(
    -- Standard columns
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Tenant isolation
    [TenantId] BIGINT NOT NULL,

    -- Relationships
    [VendorId] BIGINT NOT NULL,                   -- FK to Vendor
    [AgentRunId] BIGINT NOT NULL,                 -- FK to VendorAgentRun (which run created this)

    -- Proposal state
    [Status] VARCHAR(20) NOT NULL DEFAULT 'pending',  -- 'pending', 'approved', 'rejected', 'expired', 'applied'

    -- Agent reasoning
    [Reasoning] NVARCHAR(MAX) NOT NULL,           -- Why the agent is proposing this
    [Confidence] DECIMAL(3,2) NULL,               -- 0.00 to 1.00 confidence score

    -- Human response (populated on approval/rejection)
    [RespondedDatetime] DATETIME2(3) NULL,
    [RespondedBy] VARCHAR(200) NULL,
    [RejectionReason] NVARCHAR(MAX) NULL,         -- Required explanation if rejected

    -- Applied tracking (populated when changes are applied)
    [AppliedDatetime] DATETIME2(3) NULL,
    [AppliedBy] VARCHAR(200) NULL,

    -- Context
    [Context] NVARCHAR(MAX) NULL,                 -- JSON: additional context, vendor snapshot at proposal time

    CONSTRAINT [UQ_VendorAgentProposal_PublicId] UNIQUE ([PublicId]),
    CONSTRAINT [FK_VendorAgentProposal_AgentRun] FOREIGN KEY ([AgentRunId])
        REFERENCES [dbo].[VendorAgentRun]([Id])
);
END
GO

CREATE INDEX IX_VendorAgentProposal_VendorId ON [dbo].[VendorAgentProposal]([VendorId]);
CREATE INDEX IX_VendorAgentProposal_TenantStatus ON [dbo].[VendorAgentProposal]([TenantId], [Status]);
CREATE INDEX IX_VendorAgentProposal_AgentRunId ON [dbo].[VendorAgentProposal]([AgentRunId]);
GO




-- =============================================================================
-- Stored Procedures: VendorAgentProposal
-- =============================================================================

CREATE OR ALTER PROCEDURE CreateVendorAgentProposal
(
    @TenantId BIGINT,
    @VendorId BIGINT,
    @AgentRunId BIGINT,
    @Reasoning NVARCHAR(MAX),
    @Confidence DECIMAL(3,2) = NULL,
    @Context NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorAgentProposal] (
        [CreatedDatetime], [ModifiedDatetime], [TenantId], [VendorId],
        [AgentRunId], [Status], [Reasoning], [Confidence], [Context]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[VendorId],
        INSERTED.[AgentRunId],
        INSERTED.[Status],
        INSERTED.[Reasoning],
        INSERTED.[Confidence],
        CONVERT(VARCHAR(19), INSERTED.[RespondedDatetime], 120) AS [RespondedDatetime],
        INSERTED.[RespondedBy],
        INSERTED.[RejectionReason],
        CONVERT(VARCHAR(19), INSERTED.[AppliedDatetime], 120) AS [AppliedDatetime],
        INSERTED.[AppliedBy],
        INSERTED.[Context]
    VALUES (
        @Now, @Now, @TenantId, @VendorId,
        @AgentRunId, 'pending', @Reasoning, @Confidence, @Context
    );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ApproveVendorAgentProposal
(
    @PublicId UNIQUEIDENTIFIER,
    @RespondedBy VARCHAR(200)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorAgentProposal]
    SET
        [Status] = 'approved',
        [ModifiedDatetime] = @Now,
        [RespondedDatetime] = @Now,
        [RespondedBy] = @RespondedBy
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[VendorId],
        INSERTED.[AgentRunId],
        INSERTED.[Status],
        INSERTED.[Reasoning],
        INSERTED.[Confidence],
        CONVERT(VARCHAR(19), INSERTED.[RespondedDatetime], 120) AS [RespondedDatetime],
        INSERTED.[RespondedBy],
        INSERTED.[RejectionReason],
        CONVERT(VARCHAR(19), INSERTED.[AppliedDatetime], 120) AS [AppliedDatetime],
        INSERTED.[AppliedBy],
        INSERTED.[Context]
    WHERE [PublicId] = @PublicId AND [Status] = 'pending';

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE RejectVendorAgentProposal
(
    @PublicId UNIQUEIDENTIFIER,
    @RespondedBy VARCHAR(200),
    @RejectionReason NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorAgentProposal]
    SET
        [Status] = 'rejected',
        [ModifiedDatetime] = @Now,
        [RespondedDatetime] = @Now,
        [RespondedBy] = @RespondedBy,
        [RejectionReason] = @RejectionReason
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[VendorId],
        INSERTED.[AgentRunId],
        INSERTED.[Status],
        INSERTED.[Reasoning],
        INSERTED.[Confidence],
        CONVERT(VARCHAR(19), INSERTED.[RespondedDatetime], 120) AS [RespondedDatetime],
        INSERTED.[RespondedBy],
        INSERTED.[RejectionReason],
        CONVERT(VARCHAR(19), INSERTED.[AppliedDatetime], 120) AS [AppliedDatetime],
        INSERTED.[AppliedBy],
        INSERTED.[Context]
    WHERE [PublicId] = @PublicId AND [Status] = 'pending';

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkVendorAgentProposalApplied
(
    @PublicId UNIQUEIDENTIFIER,
    @AppliedBy VARCHAR(200)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorAgentProposal]
    SET
        [Status] = 'applied',
        [ModifiedDatetime] = @Now,
        [AppliedDatetime] = @Now,
        [AppliedBy] = @AppliedBy
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TenantId],
        INSERTED.[VendorId],
        INSERTED.[AgentRunId],
        INSERTED.[Status],
        INSERTED.[Reasoning],
        INSERTED.[Confidence],
        CONVERT(VARCHAR(19), INSERTED.[RespondedDatetime], 120) AS [RespondedDatetime],
        INSERTED.[RespondedBy],
        INSERTED.[RejectionReason],
        CONVERT(VARCHAR(19), INSERTED.[AppliedDatetime], 120) AS [AppliedDatetime],
        INSERTED.[AppliedBy],
        INSERTED.[Context]
    WHERE [PublicId] = @PublicId AND [Status] = 'approved';

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorAgentProposalByPublicId
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
        [VendorId],
        [AgentRunId],
        [Status],
        [Reasoning],
        [Confidence],
        CONVERT(VARCHAR(19), [RespondedDatetime], 120) AS [RespondedDatetime],
        [RespondedBy],
        [RejectionReason],
        CONVERT(VARCHAR(19), [AppliedDatetime], 120) AS [AppliedDatetime],
        [AppliedBy],
        [Context]
    FROM dbo.[VendorAgentProposal]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorAgentProposalsByVendor
(
    @VendorId BIGINT,
    @Status VARCHAR(20) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF @Status IS NULL
        SELECT
            [Id],
            [PublicId],
            [RowVersion],
            CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
            [TenantId],
            [VendorId],
            [AgentRunId],
            [Status],
            [Reasoning],
            [Confidence],
            CONVERT(VARCHAR(19), [RespondedDatetime], 120) AS [RespondedDatetime],
            [RespondedBy],
            [RejectionReason],
            CONVERT(VARCHAR(19), [AppliedDatetime], 120) AS [AppliedDatetime],
            [AppliedBy],
            [Context]
        FROM dbo.[VendorAgentProposal]
        WHERE [VendorId] = @VendorId
        ORDER BY [CreatedDatetime] DESC;
    ELSE
        SELECT
            [Id],
            [PublicId],
            [RowVersion],
            CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
            CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
            [TenantId],
            [VendorId],
            [AgentRunId],
            [Status],
            [Reasoning],
            [Confidence],
            CONVERT(VARCHAR(19), [RespondedDatetime], 120) AS [RespondedDatetime],
            [RespondedBy],
            [RejectionReason],
            CONVERT(VARCHAR(19), [AppliedDatetime], 120) AS [AppliedDatetime],
            [AppliedBy],
            [Context]
        FROM dbo.[VendorAgentProposal]
        WHERE [VendorId] = @VendorId AND [Status] = @Status
        ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadPendingVendorAgentProposals
(
    @TenantId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        p.[Id],
        p.[PublicId],
        p.[RowVersion],
        CONVERT(VARCHAR(19), p.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), p.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        p.[TenantId],
        p.[VendorId],
        p.[AgentRunId],
        p.[Status],
        p.[Reasoning],
        p.[Confidence],
        CONVERT(VARCHAR(19), p.[RespondedDatetime], 120) AS [RespondedDatetime],
        p.[RespondedBy],
        p.[RejectionReason],
        CONVERT(VARCHAR(19), p.[AppliedDatetime], 120) AS [AppliedDatetime],
        p.[AppliedBy],
        p.[Context]
    FROM dbo.[VendorAgentProposal] p
    WHERE p.[TenantId] = @TenantId AND p.[Status] = 'pending'
    ORDER BY p.[CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO


-- Read rejected proposals for a vendor (for agent learning)
CREATE OR ALTER PROCEDURE ReadRejectedVendorAgentProposals
(
    @VendorId BIGINT
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
        [VendorId],
        [AgentRunId],
        [Status],
        [Reasoning],
        [Confidence],
        CONVERT(VARCHAR(19), [RespondedDatetime], 120) AS [RespondedDatetime],
        [RespondedBy],
        [RejectionReason],
        CONVERT(VARCHAR(19), [AppliedDatetime], 120) AS [AppliedDatetime],
        [AppliedBy],
        [Context]
    FROM dbo.[VendorAgentProposal]
    WHERE [VendorId] = @VendorId AND [Status] = 'rejected'
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


