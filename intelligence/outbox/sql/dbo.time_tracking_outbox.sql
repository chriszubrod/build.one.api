-- ============================================================================
-- dbo.TimeTrackingOutbox — Durable queue for time_tracking_specialist agent
-- review passes on iOS-submitted TimeEntries.
--
-- When a TimeEntry transitions draft → submitted, the submit service enqueues
-- a row here. The build.one.scheduler Function App polls a tick endpoint
-- (~30s cadence) that claims the oldest pending row, kicks off the agent run,
-- and marks the row done / failed / dead-letter.
--
-- Shape mirrors ms.Outbox and qbo.Outbox but in the `dbo` schema — this is
-- an internal-only queue, not an external integration. Dropped fields vs
-- ms.Outbox:
--   - TenantId  (no external tenancy)
--   - RequestId (no external dedup header)
--   - Payload   (agent re-reads entity state from current DB at drain time)
--
-- Lifecycle: pending → in_progress → done | failed → … → dead_letter
-- 5 failed attempts → dead_letter (decision made in the worker; this schema
-- accepts whatever the worker decides via FailTimeTrackingOutbox vs
-- DeadLetterTimeTrackingOutbox).
-- ============================================================================

IF OBJECT_ID('dbo.TimeTrackingOutbox', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[TimeTrackingOutbox]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,
    [CreatedDatetime]   DATETIME2(3) NOT NULL,
    [ModifiedDatetime]  DATETIME2(3) NULL,

    -- What to do
    [Kind]              NVARCHAR(64)  NOT NULL,       -- 'review_submitted_time_entry'
    [EntityType]        NVARCHAR(32)  NOT NULL,       -- 'TimeEntry'
    [EntityPublicId]    UNIQUEIDENTIFIER NOT NULL,    -- TimeEntry.PublicId

    -- Lifecycle
    [Status]            NVARCHAR(16)  NOT NULL DEFAULT 'pending',
                                                       -- pending | in_progress | done | failed | dead_letter
    [Attempts]          INT           NOT NULL DEFAULT 0,
    [NextRetryAt]       DATETIME2(3)  NOT NULL,       -- earliest time worker may dispatch after a failure
    [ReadyAfter]        DATETIME2(3)  NULL,           -- Policy C debounce gate; NULL = ready immediately
    [LastError]         NVARCHAR(MAX) NULL,
    [CorrelationId]     UNIQUEIDENTIFIER NULL,

    -- Attempt tracking
    [StartedAt]         DATETIME2(3)  NULL,
    [CompletedAt]       DATETIME2(3)  NULL,
    [DeadLetteredAt]    DATETIME2(3)  NULL
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeTrackingOutbox_Drain' AND object_id = OBJECT_ID('dbo.TimeTrackingOutbox'))
BEGIN
    CREATE INDEX IX_TimeTrackingOutbox_Drain
        ON [dbo].[TimeTrackingOutbox] ([Status], [NextRetryAt])
        WHERE [Status] IN ('pending', 'failed');
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeTrackingOutbox_Entity' AND object_id = OBJECT_ID('dbo.TimeTrackingOutbox'))
BEGIN
    CREATE INDEX IX_TimeTrackingOutbox_Entity
        ON [dbo].[TimeTrackingOutbox] ([EntityType], [EntityPublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_TimeTrackingOutbox_PublicId' AND object_id = OBJECT_ID('dbo.TimeTrackingOutbox'))
BEGIN
    CREATE UNIQUE INDEX UQ_TimeTrackingOutbox_PublicId
        ON [dbo].[TimeTrackingOutbox] ([PublicId]);
END
GO


-- ============================================================================
-- CreateTimeTrackingOutboxRow
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[CreateTimeTrackingOutboxRow]
(
    @Kind            NVARCHAR(64),
    @EntityType      NVARCHAR(32),
    @EntityPublicId  UNIQUEIDENTIFIER,
    @ReadyAfter      DATETIME2(3) = NULL,
    @CorrelationId   UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [dbo].[TimeTrackingOutbox] (
        [CreatedDatetime], [ModifiedDatetime],
        [Kind], [EntityType], [EntityPublicId],
        [Status], [Attempts], [NextRetryAt], [ReadyAfter], [CorrelationId]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120) AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt]
    VALUES (
        @Now, NULL,
        @Kind, @EntityType, @EntityPublicId,
        'pending', 0, @Now, @ReadyAfter, @CorrelationId
    );

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- ReadTimeTrackingOutboxById
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[ReadTimeTrackingOutboxById]
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind], [EntityType], [EntityPublicId],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [dbo].[TimeTrackingOutbox]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- ReadTimeTrackingOutboxByPublicId
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[ReadTimeTrackingOutboxByPublicId]
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind], [EntityType], [EntityPublicId],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [dbo].[TimeTrackingOutbox]
    WHERE [PublicId] = @PublicId;
END;
GO


-- ============================================================================
-- ReadPendingTimeTrackingOutboxByEntity — dedup probe.
-- Used by the submit service to coalesce: if a pending row for this
-- (EntityType, EntityPublicId, Kind) already exists, skip the enqueue.
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[ReadPendingTimeTrackingOutboxByEntity]
(
    @EntityType     NVARCHAR(32),
    @EntityPublicId UNIQUEIDENTIFIER,
    @Kind           NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind], [EntityType], [EntityPublicId],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [dbo].[TimeTrackingOutbox]
    WHERE [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId
      AND [Kind]           = @Kind
      AND [Status] IN ('pending', 'failed')
    ORDER BY [Id] DESC;
END;
GO


-- ============================================================================
-- ClaimNextPendingTimeTrackingOutbox — worker dequeue path.
-- UPDLOCK + READPAST so concurrent worker ticks don't grab the same row.
-- Returns the claimed row or an empty result if nothing is ready.
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[ClaimNextPendingTimeTrackingOutbox]
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now  DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Id   BIGINT;

    BEGIN TRANSACTION;

    ;WITH candidate AS (
        SELECT TOP 1 [Id]
        FROM [dbo].[TimeTrackingOutbox] WITH (UPDLOCK, READPAST, ROWLOCK)
        WHERE [Status] IN ('pending', 'failed')
          AND [NextRetryAt] <= @Now
          AND ([ReadyAfter] IS NULL OR [ReadyAfter] <= @Now)
        ORDER BY [Id]
    )
    UPDATE o
    SET o.[Status]           = 'in_progress',
        o.[StartedAt]        = @Now,
        o.[ModifiedDatetime] = @Now,
        @Id                  = o.[Id]
    FROM [dbo].[TimeTrackingOutbox] o
    INNER JOIN candidate c ON c.[Id] = o.[Id];

    COMMIT TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind], [EntityType], [EntityPublicId],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [dbo].[TimeTrackingOutbox]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- CompleteTimeTrackingOutbox — success path.
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[CompleteTimeTrackingOutbox]
(
    @Id         BIGINT,
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [dbo].[TimeTrackingOutbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'done',
        [CompletedAt]      = @Now,
        [LastError]        = NULL
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- FailTimeTrackingOutbox — retryable failure. Worker computes NextRetryAt.
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[FailTimeTrackingOutbox]
(
    @Id          BIGINT,
    @RowVersion  BINARY(8),
    @NextRetryAt DATETIME2(3),
    @LastError   NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [dbo].[TimeTrackingOutbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'failed',
        [Attempts]         = [Attempts] + 1,
        [NextRetryAt]      = @NextRetryAt,
        [LastError]        = @LastError
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- DeadLetterTimeTrackingOutbox — terminal failure.
-- ============================================================================
CREATE OR ALTER PROCEDURE [dbo].[DeadLetterTimeTrackingOutbox]
(
    @Id         BIGINT,
    @RowVersion BINARY(8),
    @LastError  NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [dbo].[TimeTrackingOutbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'dead_letter',
        [Attempts]         = [Attempts] + 1,
        [DeadLetteredAt]   = @Now,
        [LastError]        = @LastError
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
