-- ============================================================================
-- ms.Outbox — Durable queue for MS Graph write operations.
--
-- Every local write that needs to reach Microsoft 365 (upload to SharePoint,
-- append row to Excel workbook, insert row, send mail) first inserts a row
-- here. A background worker drains pending rows and dispatches them to the
-- appropriate MsGraphClient call with the row's stable RequestId as the
-- `x-ms-client-request-id` header value. Microsoft Graph deduplicates
-- retries using that header, so a crashed or timed-out attempt cannot
-- cause duplicate side effects.
--
-- Differences vs [qbo].[Outbox]:
--   - TenantId instead of RealmId (MS tenancy model).
--   - Nullable Payload NVARCHAR(MAX) column for upload-session state
--     (uploadUrl + completed_bytes for resumable uploads; Phase 3, task 3.5).
--     Other Kinds leave Payload NULL and re-read entity state at drain time.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ms')
    EXEC('CREATE SCHEMA ms AUTHORIZATION dbo;');
GO


IF OBJECT_ID('ms.Outbox', 'U') IS NULL
BEGIN
CREATE TABLE [ms].[Outbox]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,
    [CreatedDatetime]   DATETIME2(3) NOT NULL,
    [ModifiedDatetime]  DATETIME2(3) NULL,

    -- What to do
    [Kind]              NVARCHAR(64)  NOT NULL,       -- 'upload_sharepoint_file' | 'append_excel_row' |
                                                       -- 'insert_excel_row' | 'send_mail'
    [EntityType]        NVARCHAR(32)  NOT NULL,       -- 'BillAttachment' | 'ExpenseAttachment' |
                                                       -- 'Bill' | 'Expense' | etc.
    [EntityPublicId]    UNIQUEIDENTIFIER NOT NULL,    -- public_id of the local entity
    [TenantId]          NVARCHAR(64)  NOT NULL,
    [RequestId]         UNIQUEIDENTIFIER NOT NULL,    -- stable across retries; used as x-ms-client-request-id

    -- Optional handler-specific state. Nullable. Used by upload_sharepoint_file
    -- to persist upload-session state (uploadUrl, completed_bytes). Other
    -- Kinds leave this NULL and re-read entity state at drain time.
    [Payload]           NVARCHAR(MAX) NULL,

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


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsOutbox_Drain' AND object_id = OBJECT_ID('ms.Outbox'))
BEGIN
    CREATE INDEX IX_MsOutbox_Drain
        ON [ms].[Outbox] ([Status], [NextRetryAt])
        WHERE [Status] IN ('pending', 'failed');
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsOutbox_Entity' AND object_id = OBJECT_ID('ms.Outbox'))
BEGIN
    CREATE INDEX IX_MsOutbox_Entity
        ON [ms].[Outbox] ([EntityType], [EntityPublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_MsOutbox_PublicId' AND object_id = OBJECT_ID('ms.Outbox'))
BEGIN
    CREATE UNIQUE INDEX UQ_MsOutbox_PublicId
        ON [ms].[Outbox] ([PublicId]);
END
GO


-- ============================================================================
-- CreateMsOutbox
-- ============================================================================
CREATE OR ALTER PROCEDURE CreateMsOutbox
(
    @Kind            NVARCHAR(64),
    @EntityType      NVARCHAR(32),
    @EntityPublicId  UNIQUEIDENTIFIER,
    @TenantId        NVARCHAR(64),
    @RequestId       UNIQUEIDENTIFIER,
    @Payload         NVARCHAR(MAX) = NULL,
    @ReadyAfter      DATETIME2(3) = NULL,
    @CorrelationId   UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[Outbox] (
        [CreatedDatetime], [ModifiedDatetime],
        [Kind], [EntityType], [EntityPublicId],
        [TenantId], [RequestId], [Payload],
        [Status], [Attempts], [NextRetryAt], [ReadyAfter], [CorrelationId]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[TenantId], INSERTED.[RequestId], INSERTED.[Payload],
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
        @TenantId, @RequestId, @Payload,
        'pending', 0, @Now, @ReadyAfter, @CorrelationId
    );

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- ReadMsOutboxById
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadMsOutboxById
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
        [TenantId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [ms].[Outbox]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- ReadMsOutboxByPublicId
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadMsOutboxByPublicId
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
        [TenantId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [ms].[Outbox]
    WHERE [PublicId] = @PublicId;
END;
GO


-- ============================================================================
-- ReadPendingMsOutboxByEntity
-- Policy C coalesce: find an existing pending/failed row for the same
-- (EntityType, EntityPublicId, Kind). Only upload_sharepoint_file coalesces
-- on the service side; excel_* kinds always create fresh rows even if this
-- sproc would return a match. The sproc doesn't enforce that — it's up to
-- the service layer to decide whether to call this or not.
-- ============================================================================
CREATE OR ALTER PROCEDURE ReadPendingMsOutboxByEntity
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
        [TenantId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [ms].[Outbox]
    WHERE [EntityType]     = @EntityType
      AND [EntityPublicId] = @EntityPublicId
      AND [Kind]           = @Kind
      AND [Status] IN ('pending', 'failed')
    ORDER BY [Id] DESC;
END;
GO


-- ============================================================================
-- UpdateMsOutboxReadyAfter (Policy C debounce extension)
-- ============================================================================
CREATE OR ALTER PROCEDURE UpdateMsOutboxReadyAfter
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

    UPDATE [ms].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [ReadyAfter]       = @ReadyAfter
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[TenantId], INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120) AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- UpdateMsOutboxPayload (used by upload-session resume)
-- Updates Payload without touching lifecycle columns. Uses ROWVERSION guard.
-- ============================================================================
CREATE OR ALTER PROCEDURE UpdateMsOutboxPayload
(
    @Id          BIGINT,
    @RowVersion  BINARY(8),
    @Payload     NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [ms].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Payload]          = @Payload
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Kind], INSERTED.[EntityType], INSERTED.[EntityPublicId],
        INSERTED.[TenantId], INSERTED.[RequestId], INSERTED.[Payload],
        INSERTED.[Status], INSERTED.[Attempts],
        CONVERT(VARCHAR(19), INSERTED.[NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), INSERTED.[ReadyAfter], 120) AS [ReadyAfter],
        INSERTED.[LastError], INSERTED.[CorrelationId],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), INSERTED.[DeadLetteredAt], 120) AS [DeadLetteredAt]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- ClaimNextPendingMsOutbox (worker's dequeue path)
-- Mirrors QBO: UPDLOCK+READPAST so concurrent worker ticks don't grab the
-- same row. Returns the claimed row or empty result if nothing ready.
-- ============================================================================
CREATE OR ALTER PROCEDURE ClaimNextPendingMsOutbox
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now  DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Id   BIGINT;

    BEGIN TRANSACTION;

    ;WITH candidate AS (
        SELECT TOP 1 [Id]
        FROM [ms].[Outbox] WITH (UPDLOCK, READPAST, ROWLOCK)
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
    FROM [ms].[Outbox] o
    INNER JOIN candidate c ON c.[Id] = o.[Id];

    COMMIT TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Kind], [EntityType], [EntityPublicId],
        [TenantId], [RequestId], [Payload],
        [Status], [Attempts],
        CONVERT(VARCHAR(19), [NextRetryAt], 120) AS [NextRetryAt],
        CONVERT(VARCHAR(19), [ReadyAfter], 120) AS [ReadyAfter],
        [LastError], [CorrelationId],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt],
        CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS [DeadLetteredAt]
    FROM [ms].[Outbox]
    WHERE [Id] = @Id;
END;
GO


-- ============================================================================
-- CompleteMsOutbox
-- ============================================================================
CREATE OR ALTER PROCEDURE CompleteMsOutbox
(
    @Id         BIGINT,
    @RowVersion BINARY(8)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [ms].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'done',
        [CompletedAt]      = @Now,
        [LastError]        = NULL
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ============================================================================
-- FailMsOutbox (retryable failure; worker computes NextRetryAt)
-- ============================================================================
CREATE OR ALTER PROCEDURE FailMsOutbox
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

    UPDATE [ms].[Outbox]
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
-- DeadLetterMsOutbox
-- ============================================================================
CREATE OR ALTER PROCEDURE DeadLetterMsOutbox
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

    UPDATE [ms].[Outbox]
    SET [ModifiedDatetime] = @Now,
        [Status]           = 'dead_letter',
        [Attempts]         = [Attempts] + 1,
        [DeadLetteredAt]   = @Now,
        [LastError]        = @LastError
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO
