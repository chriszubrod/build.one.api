-- ============================================================================
-- box.ReconciliationIssue — record of drift / failures detected in the Box
-- integration layer (file pushes, folder mappings).
--
-- Primary writer (Phase 2): the BoxOutboxWorker dead-letter hook — when a
-- Box outbox row dead-letters, BoxReconciliationIssueService.flag_dead_letter
-- escalates it to a ReconciliationIssue (Severity 'critical' for the
-- upload_box_file kind) instead of silently accepting the dead-letter.
-- A future daily reconcile job ("locally complete but not in Box") gets the
-- same table.
--
-- Column set is an EXACT copy of [ms].[ReconciliationIssue] (which itself
-- mirrors [qbo].[ReconciliationIssue]) so a future consolidated review UI
-- can query all three tables with uniform shape. Box semantics for the
-- carried-over columns:
--   - [TenantId]      → the Box enterprise id (or the literal 'box' when the
--                       caller has no enterprise id in hand).
--   - [DriveItemId]   → the external-pointer slot; carries the BoxFileId or
--                       BoxFolderId string when relevant.
--   - [WorksheetName] → unused for Box today; kept NULL for shape parity.
--
-- RUN ORDER: box.outbox.sql runs FIRST (it owns the CREATE SCHEMA [box]
-- guard). This file assumes the [box] schema exists.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'box')
    EXEC('CREATE SCHEMA box AUTHORIZATION dbo;');
GO

IF OBJECT_ID('box.ReconciliationIssue', 'U') IS NULL
BEGIN
CREATE TABLE [box].[ReconciliationIssue]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,
    [CreatedDatetime]   DATETIME2(3) NOT NULL,
    [ModifiedDatetime]  DATETIME2(3) NULL,

    -- What drift was found
    [DriftType]         NVARCHAR(32)  NOT NULL,   -- 'upload_box_file_dead_letter' | 'outbox_dead_letter' | future drift kinds
    [Severity]          NVARCHAR(16)  NOT NULL,   -- 'low' | 'medium' | 'high' | 'critical'
    [Action]            NVARCHAR(16)  NOT NULL,   -- 'auto_fixed' | 'flagged'

    -- What entity the drift relates to
    [EntityType]        NVARCHAR(32)  NOT NULL,   -- 'bill' | 'bill_credit' | 'expense' | 'invoice' | etc.
    [EntityPublicId]    UNIQUEIDENTIFIER NULL,    -- local entity public_id
    [TenantId]          NVARCHAR(64)  NOT NULL,   -- Box enterprise id (or 'box')

    -- External pointers (nullable; populated when relevant)
    [DriveItemId]       NVARCHAR(256) NULL,       -- BoxFileId / BoxFolderId string when known
    [WorksheetName]     NVARCHAR(128) NULL,       -- unused for Box; shape parity with ms
    [OutboxPublicId]    UNIQUEIDENTIFIER NULL,    -- dead-lettered [box].[Outbox] row (for traceback)

    -- Diagnostic text
    [Details]           NVARCHAR(MAX) NULL,

    -- Review lifecycle
    [Status]            NVARCHAR(16)  NOT NULL DEFAULT 'open',
                                                    -- 'open' | 'acknowledged' | 'resolved'
    [AcknowledgedAt]    DATETIME2(3)  NULL,
    [ResolvedAt]        DATETIME2(3)  NULL,

    -- Run correlation
    [ReconcileRunId]    UNIQUEIDENTIFIER NULL
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BoxReconciliationIssue_Status' AND object_id = OBJECT_ID('box.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_BoxReconciliationIssue_Status
        ON [box].[ReconciliationIssue] ([Status], [Severity])
        WHERE [Action] = 'flagged';
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BoxReconciliationIssue_Entity' AND object_id = OBJECT_ID('box.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_BoxReconciliationIssue_Entity
        ON [box].[ReconciliationIssue] ([EntityType], [EntityPublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BoxReconciliationIssue_Run' AND object_id = OBJECT_ID('box.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_BoxReconciliationIssue_Run
        ON [box].[ReconciliationIssue] ([ReconcileRunId]);
END
GO


CREATE OR ALTER PROCEDURE CreateBoxReconciliationIssue
(
    @DriftType       NVARCHAR(32),
    @Severity        NVARCHAR(16),
    @Action          NVARCHAR(16),
    @EntityType      NVARCHAR(32),
    @EntityPublicId  UNIQUEIDENTIFIER = NULL,
    @TenantId        NVARCHAR(64),
    @DriveItemId     NVARCHAR(256) = NULL,
    @WorksheetName   NVARCHAR(128) = NULL,
    @OutboxPublicId  UNIQUEIDENTIFIER = NULL,
    @Details         NVARCHAR(MAX) = NULL,
    @ReconcileRunId  UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    -- NOCOUNT is load-bearing for pyodbc: without it, DML row-count tokens
    -- arrive as the first "result" and fetchone() never reaches the rows.
    SET NOCOUNT ON;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [box].[ReconciliationIssue] (
        [CreatedDatetime], [ModifiedDatetime],
        [DriftType], [Severity], [Action],
        [EntityType], [EntityPublicId], [TenantId],
        [DriveItemId], [WorksheetName], [OutboxPublicId],
        [Details], [Status], [ReconcileRunId]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[DriftType], INSERTED.[Severity], INSERTED.[Action],
        INSERTED.[EntityType], INSERTED.[EntityPublicId], INSERTED.[TenantId],
        INSERTED.[DriveItemId], INSERTED.[WorksheetName], INSERTED.[OutboxPublicId],
        INSERTED.[Details], INSERTED.[Status], INSERTED.[ReconcileRunId],
        CONVERT(VARCHAR(19), INSERTED.[AcknowledgedAt], 120) AS [AcknowledgedAt],
        CONVERT(VARCHAR(19), INSERTED.[ResolvedAt], 120) AS [ResolvedAt]
    VALUES (
        @Now, NULL,
        @DriftType, @Severity, @Action,
        @EntityType, @EntityPublicId, @TenantId,
        @DriveItemId, @WorksheetName, @OutboxPublicId,
        @Details, 'open', @ReconcileRunId
    );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadBoxReconciliationIssuesByStatus
(
    @Status NVARCHAR(16)
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [DriftType], [Severity], [Action],
        [EntityType], [EntityPublicId], [TenantId],
        [DriveItemId], [WorksheetName], [OutboxPublicId],
        [Details], [Status], [ReconcileRunId],
        CONVERT(VARCHAR(19), [AcknowledgedAt], 120) AS [AcknowledgedAt],
        CONVERT(VARCHAR(19), [ResolvedAt], 120) AS [ResolvedAt]
    FROM [box].[ReconciliationIssue]
    WHERE [Status] = @Status
    ORDER BY [CreatedDatetime] DESC;
END;
GO


CREATE OR ALTER PROCEDURE CountBoxReconciliationIssues
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [DriftType], [Severity], [Action], [Status],
        COUNT(*) AS [Count]
    FROM [box].[ReconciliationIssue]
    GROUP BY [DriftType], [Severity], [Action], [Status]
    ORDER BY [DriftType], [Severity];
END;
GO
