-- ============================================================================
-- qbo.Outbox — Durable queue for QBO write operations.
--
-- Every local write that needs to reach QBO (push Bill, push Expense, etc.)
-- first inserts a row here in the same local transaction that mutates the
-- entity. A background worker (task #14d/14e) drains pending rows, calling
-- the appropriate QBO client with the row's stable RequestId as the
-- idempotency key. QBO deduplicates retries on RequestId, so a crashed
-- or timed-out attempt cannot create a duplicate bill on the QBO side.
--
-- Policy C coalescing: multiple edits to the same entity within a short
-- window collapse into a single outbox row (see QboOutboxService.enqueue).
-- Debounce is enforced via the ReadyAfter column; the worker only dispatches
-- rows where (NextRetryAt <= now) AND (ReadyAfter IS NULL OR ReadyAfter <= now).
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'qbo')
    EXEC('CREATE SCHEMA qbo AUTHORIZATION dbo;');
GO


IF OBJECT_ID('qbo.Outbox', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Outbox]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,
    [CreatedDatetime]   DATETIME2(3) NOT NULL,
    [ModifiedDatetime]  DATETIME2(3) NULL,

    -- What to do
    [Kind]              NVARCHAR(64)  NOT NULL,       -- e.g., 'sync_bill_to_qbo'
    [EntityType]        NVARCHAR(32)  NOT NULL,       -- 'Bill' | 'Expense' | 'Invoice' | 'BillCredit'
    [EntityPublicId]    UNIQUEIDENTIFIER NOT NULL,    -- public_id of the local entity
    [RealmId]           NVARCHAR(64)  NOT NULL,
    [RequestId]         UNIQUEIDENTIFIER NOT NULL,    -- idempotency key; stable across retries

    -- Lifecycle
    [Status]            NVARCHAR(16)  NOT NULL DEFAULT 'pending',
                                                       -- pending | in_progress | done | failed | dead_letter
    [Attempts]          INT           NOT NULL DEFAULT 0,
    [NextRetryAt]       DATETIME2(3)  NOT NULL,       -- earliest time worker may dispatch after a failure
    [ReadyAfter]        DATETIME2(3)  NULL,           -- Policy C debounce gate; NULL means ready immediately
    [LastError]         NVARCHAR(MAX) NULL,
    [CorrelationId]     UNIQUEIDENTIFIER NULL,

    -- Attempt tracking
    [StartedAt]         DATETIME2(3)  NULL,
    [CompletedAt]       DATETIME2(3)  NULL,
    [DeadLetteredAt]    DATETIME2(3)  NULL
);
END
GO


-- Drain index: the worker's hot path.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Outbox_Drain' AND object_id = OBJECT_ID('qbo.Outbox'))
BEGIN
    CREATE INDEX IX_Outbox_Drain
        ON [qbo].[Outbox] ([Status], [NextRetryAt])
        WHERE [Status] IN ('pending', 'failed');
END
GO


-- Coalesce-lookup index: used by enqueue to find an existing row for the same entity.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Outbox_Entity' AND object_id = OBJECT_ID('qbo.Outbox'))
BEGIN
    CREATE INDEX IX_Outbox_Entity
        ON [qbo].[Outbox] ([EntityType], [EntityPublicId]);
END
GO


-- PublicId lookup (for API surfaces and runbook diagnosis).
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Outbox_PublicId' AND object_id = OBJECT_ID('qbo.Outbox'))
BEGIN
    CREATE UNIQUE INDEX UQ_Outbox_PublicId
        ON [qbo].[Outbox] ([PublicId]);
END
GO


-- ============================================================================
-- CreateQboOutbox
-- Inserts a new pending outbox row. `ReadyAfter` enforces the initial
-- Policy C debounce window; `NextRetryAt` starts at now (no backoff yet).
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateQboOutbox
(
    @Kind            NVARCHAR(64),
    @EntityType      NVARCHAR(32),
    @EntityPublicId  UNIQUEIDENTIFIER,
    @RealmId         NVARCHAR(64),
    @RequestId       UNIQUEIDENTIFIER,
    @ReadyAfter      DATETIME2(3) = NULL,
    @CorrelationId   UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Outbox] (
        [CreatedDatetime],
        [ModifiedDatetime],
        [Kind],
        [EntityType],
        [EntityPublicId],
        [RealmId],
        [RequestId],
        [Status],
        [Attempts],
        [NextRetryAt],
        [ReadyAfter],
        [CorrelationId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Kind],
        INSERTED.[EntityType],
        INSERTED.[EntityPublicId],
        INSERTED.[RealmId],
        INSERTED.[RequestId],
        INSERTED.[Status],
        INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120) AS [ReadyAfter],
        INSERTED.[LastError],
        INSERTED.[CorrelationId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt]
    VALUES (
        @Now,
        NULL,
        @Kind,
        @EntityType,
        @EntityPublicId,
        @RealmId,
        @RequestId,
        'pending',
        0,
        @Now,                 -- NextRetryAt starts at now (no retry backoff yet)
        @ReadyAfter,          -- Policy C debounce; NULL = ready immediately
        @CorrelationId
    );

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- ReadQboOutboxById
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadQboOutboxById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind],
        [EntityType],
        [EntityPublicId],
        [RealmId],
        [RequestId],
        [Status],
        [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError],
        [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [qbo].[Outbox]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- ReadQboOutboxByPublicId
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadQboOutboxByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind],
        [EntityType],
        [EntityPublicId],
        [RealmId],
        [RequestId],
        [Status],
        [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError],
        [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [qbo].[Outbox]
    WHERE [PublicId] = @PublicId;
END;
GO


-- ============================================================================
-- ReadPendingQboOutboxByEntity
-- For Policy C coalesce: find an existing pending/failed row for the same
-- (EntityType, EntityPublicId, Kind). Returns at most one row (the most recent).
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadPendingQboOutboxByEntity
(
    @EntityType     NVARCHAR(32),
    @EntityPublicId UNIQUEIDENTIFIER,
    @Kind           NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind],
        [EntityType],
        [EntityPublicId],
        [RealmId],
        [RequestId],
        [Status],
        [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError],
        [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [qbo].[Outbox]
    WHERE [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId
      AND [Kind]           = @Kind
      AND [Status] IN ('pending', 'failed')
    ORDER BY [Id] DESC;
END;
GO


-- ============================================================================
-- UpdateQboOutboxReadyAfter
-- Extend the Policy C debounce window on an existing pending/failed row
-- when a new edit coalesces into it.
-- ============================================================================
CREATE OR ALTER PROCEDURE UpdateQboOutboxReadyAfter
(
    @Id          BIGINT,
    @RowVersion  BINARY(8),
    @ReadyAfter  DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [ReadyAfter]       = @ReadyAfter
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Kind],
        INSERTED.[EntityType],
        INSERTED.[EntityPublicId],
        INSERTED.[RealmId],
        INSERTED.[RequestId],
        INSERTED.[Status],
        INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120) AS [ReadyAfter],
        INSERTED.[LastError],
        INSERTED.[CorrelationId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- ClaimNextPendingQboOutbox
-- The worker's dequeue path. Atomically selects the oldest row that is:
--   - Status in ('pending', 'failed')
--   - NextRetryAt in the past (retry-backoff has elapsed)
--   - ReadyAfter NULL or in the past (Policy C debounce has elapsed)
-- and marks it 'in_progress'. Uses UPDLOCK + READPAST so concurrent worker
-- ticks don't claim the same row (READPAST skips rows already locked by
-- another session's in-flight claim).
-- Returns the claimed row, or empty result if nothing is ready.
-- ============================================================================
CREATE OR ALTER PROCEDURE ClaimNextPendingQboOutbox
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now  DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Id   BIGINT;

    BEGIN TRANSACTION;

    ;WITH candidate AS (
        SELECT TOP 1 [Id]
        FROM [qbo].[Outbox] WITH (UPDLOCK, READPAST, ROWLOCK)
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
    FROM [qbo].[Outbox] o
    INNER JOIN candidate c ON c.[Id] = o.[Id];

    IF @Id IS NULL
    BEGIN
        COMMIT TRANSACTION;
        RETURN;
    END;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind],
        [EntityType],
        [EntityPublicId],
        [RealmId],
        [RequestId],
        [Status],
        [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError],
        [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [qbo].[Outbox]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- CompleteQboOutbox
-- Worker calls this after a successful drain.
-- ============================================================================
CREATE OR ALTER PROCEDURE CompleteQboOutbox
(
    @Id         BIGINT,
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'done',
        [CompletedAt]      = @Now,
        [LastError]        = NULL
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- FailQboOutbox
-- Worker calls this after a retryable failure. Schedules next retry and
-- increments attempt count. Caller decides NextRetryAt using the retry
-- policy (computed in Python).
-- ============================================================================
CREATE OR ALTER PROCEDURE FailQboOutbox
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

    UPDATE [qbo].[Outbox]
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
-- DeadLetterQboOutbox
-- Worker calls this when retries are exhausted or the failure is non-retryable.
-- Row stops being picked up by the drain and waits for human triage.
-- ============================================================================
CREATE OR ALTER PROCEDURE DeadLetterQboOutbox
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

    UPDATE [qbo].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'dead_letter',
        [Attempts]         = [Attempts] + 1,
        [DeadLetteredAt]   = @Now,
        [LastError]        = @LastError
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
