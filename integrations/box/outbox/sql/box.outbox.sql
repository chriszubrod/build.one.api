-- ============================================================================
-- box.Outbox — Durable queue for Box write operations (Phase 2).
--
-- Every local write that needs to reach Box (file upload, future Excel-style
-- updates) first inserts a row here. The BoxOutboxWorker drains pending rows
-- and dispatches them to BoxHttpClient calls; the row's stable RequestId is
-- the idempotency key carried across retries.
--
-- RUN ORDER: this file runs FIRST — it owns the CREATE SCHEMA [box] guard.
-- box.folder.sql / box.file.sql / box.reconciliation_issue.sql depend on the
-- [box] schema existing.
--
-- Differences vs [ms].[Outbox] (deliberate, per the Phase-2 contract):
--   - No TenantId column (single Box enterprise; service-account auth).
--   - CreatedByUserId BIGINT NOT NULL DEFAULT 17 FK dbo.[User] (Gap-2 style
--     attribution; worker forwards it as the push actor).
--   - NextRetryAt is NULL until a failure schedules a retry (ms seeds it to
--     now); ReadyAfter is NOT NULL DEFAULT SYSUTCDATETIME() (ms allows NULL).
--   - Attempts increments at CLAIM time (ClaimNextPendingBoxOutbox), not at
--     fail/dead-letter time — Fail/DeadLetter do NOT touch Attempts.
--   - Every mutation sproc ends with a SELECT/OUTPUT of the affected row;
--     a RowVersion mismatch yields an EMPTY result set (never ROLLBACK
--     in-proc — pyodbc runs autocommit-off; see CLAUDE.md result-set
--     discipline + the Budget sprocs).
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'box')
    EXEC('CREATE SCHEMA box AUTHORIZATION dbo;');
GO


IF OBJECT_ID('box.Outbox', 'U') IS NULL
BEGIN
CREATE TABLE [box].[Outbox]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_BoxOutbox_PublicId DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,

    -- What to do
    [Kind]              NVARCHAR(64)  NOT NULL,        -- 'upload_box_file' | 'update_box_excel' (constant only in Phase 2)
    [EntityType]        NVARCHAR(64)  NOT NULL,        -- 'bill' | 'bill_credit' | 'expense' | 'invoice'
    [EntityPublicId]    UNIQUEIDENTIFIER NOT NULL,     -- public_id of the local entity
    [RequestId]         UNIQUEIDENTIFIER NOT NULL,     -- stable across retries; idempotency key
    [Payload]           NVARCHAR(MAX) NULL,            -- JSON: blob_path/filename/content_type/box_folder_id/attachment_id/doc_kind/project_id

    -- Lifecycle
    [Status]            NVARCHAR(32)  NOT NULL CONSTRAINT DF_BoxOutbox_Status DEFAULT 'pending',
                                                       -- pending | in_progress | done | failed | dead_letter
    [Attempts]          INT           NOT NULL CONSTRAINT DF_BoxOutbox_Attempts DEFAULT 0,
    [NextRetryAt]       DATETIME2(3)  NULL,            -- NULL until a failure schedules a retry
    [ReadyAfter]        DATETIME2(3)  NOT NULL CONSTRAINT DF_BoxOutbox_ReadyAfter DEFAULT SYSUTCDATETIME(),
                                                       -- Policy C debounce gate
    [LastError]         NVARCHAR(2048) NULL,
    [CorrelationId]     NVARCHAR(64)  NULL,
    [CreatedByUserId]   BIGINT        NOT NULL CONSTRAINT DF_BoxOutbox_CreatedByUserId DEFAULT (17),

    -- Attempt tracking
    [StartedAt]         DATETIME2(3)  NULL,
    [CompletedAt]       DATETIME2(3)  NULL,
    [DeadLetteredAt]    DATETIME2(3)  NULL,

    [CreatedDatetime]   DATETIME2(3)  NOT NULL CONSTRAINT DF_BoxOutbox_CreatedDatetime DEFAULT SYSUTCDATETIME(),
    [ModifiedDatetime]  DATETIME2(3)  NOT NULL CONSTRAINT DF_BoxOutbox_ModifiedDatetime DEFAULT SYSUTCDATETIME()
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BoxOutbox_CreatedByUser')
BEGIN
    ALTER TABLE [box].[Outbox]
    ADD CONSTRAINT [FK_BoxOutbox_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_BoxOutbox_PublicId' AND object_id = OBJECT_ID('box.Outbox'))
BEGIN
    CREATE UNIQUE INDEX UQ_BoxOutbox_PublicId
        ON [box].[Outbox] ([PublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BoxOutbox_Drain' AND object_id = OBJECT_ID('box.Outbox'))
BEGIN
    CREATE INDEX IX_BoxOutbox_Drain
        ON [box].[Outbox] ([Status], [NextRetryAt])
        WHERE [Status] IN ('pending', 'failed');
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BoxOutbox_Entity' AND object_id = OBJECT_ID('box.Outbox'))
BEGIN
    CREATE INDEX IX_BoxOutbox_Entity
        ON [box].[Outbox] ([EntityType], [EntityPublicId]);
END
GO


-- ============================================================================
-- CreateBoxOutbox
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateBoxOutbox
(
    @Kind            NVARCHAR(64),
    @EntityType      NVARCHAR(64),
    @EntityPublicId  UNIQUEIDENTIFIER,
    @RequestId       UNIQUEIDENTIFIER,
    @Payload         NVARCHAR(MAX) = NULL,
    @ReadyAfter      DATETIME2(3)  = NULL,
    @CorrelationId   NVARCHAR(64)  = NULL,
    @CreatedByUserId BIGINT        = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the rows.
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [box].[Outbox] (
        [Kind], [EntityType], [EntityPublicId], [RequestId], [Payload],
        [Status], [Attempts], [NextRetryAt], [ReadyAfter], [CorrelationId],
        [CreatedByUserId], [CreatedDatetime], [ModifiedDatetime]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120)  AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId], INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    VALUES (
        @Kind, @EntityType, @EntityPublicId, @RequestId, @Payload,
        'pending', 0, NULL, COALESCE(@ReadyAfter, @Now), @CorrelationId,
        COALESCE(@CreatedByUserId, 17), @Now, @Now
    );

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- ReadBoxOutboxById
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxOutboxById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        [Kind], [EntityType], [EntityPublicId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120)  AS [ReadyAfter],
        [LastError], [CorrelationId], [CreatedByUserId],
        CONVERT(VARCHAR(19), [StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM [box].[Outbox]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- ReadBoxOutboxByPublicId
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadBoxOutboxByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        [Kind], [EntityType], [EntityPublicId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120)  AS [ReadyAfter],
        [LastError], [CorrelationId], [CreatedByUserId],
        CONVERT(VARCHAR(19), [StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM [box].[Outbox]
    WHERE [PublicId] = @PublicId;
END;
GO


-- ============================================================================
-- ReadPendingBoxOutboxByEntity
-- Policy C coalesce lookup. Returns ALL pending/failed rows for the
-- (EntityType, EntityPublicId, Kind) triple — NOT TOP 1 like the ms variant —
-- because BoxOutboxService coalesces only the row whose Payload attachment_id
-- matches; the service scans payloads in Python. Newest first.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadPendingBoxOutboxByEntity
(
    @EntityType     NVARCHAR(64),
    @EntityPublicId UNIQUEIDENTIFIER,
    @Kind           NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id], [PublicId], [RowVersion],
        [Kind], [EntityType], [EntityPublicId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120)  AS [ReadyAfter],
        [LastError], [CorrelationId], [CreatedByUserId],
        CONVERT(VARCHAR(19), [StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM [box].[Outbox]
    WHERE [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId
      AND [Kind]           = @Kind
      AND [Status] IN ('pending', 'failed')
    ORDER BY [Id] DESC;
END;
GO


-- ============================================================================
-- ClaimNextPendingBoxOutbox (worker's dequeue path)
-- UPDLOCK+READPAST+ROWLOCK so concurrent worker ticks don't grab the same
-- row. Attempts increments HERE (claim time) per the Phase-2 contract —
-- Fail/DeadLetter do NOT increment again. Returns the claimed row (with its
-- fresh post-claim RowVersion) or an EMPTY result set when nothing is ready.
-- ============================================================================
CREATE OR ALTER PROCEDURE ClaimNextPendingBoxOutbox
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Id  BIGINT;

    BEGIN TRANSACTION;

    ;WITH candidate AS (
        SELECT TOP 1 [Id]
        FROM [box].[Outbox] WITH (UPDLOCK, READPAST, ROWLOCK)
        WHERE [Status] IN ('pending', 'failed')
          AND [ReadyAfter] <= @Now
          AND ([NextRetryAt] IS NULL OR [NextRetryAt] <= @Now)
        ORDER BY [Id]
    )
    UPDATE o
    SET o.[Status]           = 'in_progress',
        o.[Attempts]         = o.[Attempts] + 1,
        o.[StartedAt]        = @Now,
        o.[ModifiedDatetime] = @Now,
        @Id                  = o.[Id]
    FROM [box].[Outbox] o
    INNER JOIN candidate c ON c.[Id] = o.[Id];

    COMMIT TRANSACTION;

    -- Empty result set when @Id stays NULL (nothing claimable).
    SELECT
        [Id], [PublicId], [RowVersion],
        [Kind], [EntityType], [EntityPublicId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120)  AS [ReadyAfter],
        [LastError], [CorrelationId], [CreatedByUserId],
        CONVERT(VARCHAR(19), [StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM [box].[Outbox]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- UpdateBoxOutboxReadyAfter (Policy C debounce extension)
-- RowVersion mismatch → empty result set (service falls back to returning
-- the existing row — the in-flight/claimed row pushes the same blob, so a
-- lost coalesce race needs no fresh enqueue).
-- ============================================================================
CREATE OR ALTER PROCEDURE UpdateBoxOutboxReadyAfter
(
    @Id         BIGINT,
    @ReadyAfter DATETIME2(3),
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [box].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [ReadyAfter]       = @ReadyAfter
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120)  AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId], INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- UpdateBoxOutboxPayload
-- Updates Payload without touching lifecycle columns. RowVersion guard;
-- mismatch → empty result set.
-- ============================================================================
CREATE OR ALTER PROCEDURE UpdateBoxOutboxPayload
(
    @Id         BIGINT,
    @Payload    NVARCHAR(MAX),
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [box].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Payload]          = @Payload
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120)  AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId], INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- CompleteBoxOutbox
-- RowVersion mismatch → empty result set.
-- ============================================================================
CREATE OR ALTER PROCEDURE CompleteBoxOutbox
(
    @Id         BIGINT,
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [box].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'done',
        [CompletedAt]      = @Now,
        [LastError]        = NULL
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120)  AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId], INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- FailBoxOutbox (retryable failure; worker computes @NextRetryAt backoff)
-- Attempts was already incremented at claim time — NOT incremented here.
-- RowVersion mismatch → empty result set.
-- ============================================================================
CREATE OR ALTER PROCEDURE FailBoxOutbox
(
    @Id          BIGINT,
    @LastError   NVARCHAR(2048),
    @NextRetryAt DATETIME2(3),
    @RowVersion  BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [box].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'failed',
        [NextRetryAt]      = @NextRetryAt,
        [LastError]        = @LastError
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120)  AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId], INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- DeadLetterBoxOutbox
-- Attempts was already incremented at claim time — NOT incremented here.
-- RowVersion mismatch → empty result set.
-- ============================================================================
CREATE OR ALTER PROCEDURE DeadLetterBoxOutbox
(
    @Id         BIGINT,
    @LastError  NVARCHAR(2048),
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [box].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'dead_letter',
        [DeadLetteredAt]   = @Now,
        [LastError]        = @LastError
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120)  AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId], INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120)      AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120)    AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
