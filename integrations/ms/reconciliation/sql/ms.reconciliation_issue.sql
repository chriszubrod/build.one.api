-- ============================================================================
-- ms.ReconciliationIssue — record of drift / failures detected in the MS
-- integration layer (SharePoint, Excel, Mail).
--
-- Two primary writers:
--   1. Daily Excel reconciliation job (task 3.7): detects "bill completed
--      but no matching row in Excel workbook" drift. DriftType =
--      'excel_row_missing', Severity = 'high', Action = 'flagged'.
--   2. Outbox worker dead-letter hook (task 3.8): when an Excel-bound
--      outbox row (append_excel_row / insert_excel_row) dead-letters,
--      escalate to a ReconciliationIssue with Severity = 'critical'
--      instead of silently accepting the dead-letter. The user's explicit
--      requirement: failed Excel outbox entries must be followed up.
--
-- Mirrors qbo.ReconciliationIssue schema so a future consolidated review UI
-- can query both tables with uniform shape.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ms')
    EXEC('CREATE SCHEMA ms AUTHORIZATION dbo;');
GO


IF OBJECT_ID('ms.ReconciliationIssue', 'U') IS NULL
BEGIN
CREATE TABLE [ms].[ReconciliationIssue]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,
    [CreatedDatetime]   DATETIME2(3) NOT NULL,
    [ModifiedDatetime]  DATETIME2(3) NULL,

    -- What drift was found
    [DriftType]         NVARCHAR(32)  NOT NULL,   -- 'excel_row_missing' | 'excel_write_dead_letter' |
                                                    -- 'sharepoint_upload_dead_letter' | 'mail_send_dead_letter'
    [Severity]          NVARCHAR(16)  NOT NULL,   -- 'low' | 'medium' | 'high' | 'critical'
    [Action]            NVARCHAR(16)  NOT NULL,   -- 'auto_fixed' | 'flagged'

    -- What entity the drift relates to
    [EntityType]        NVARCHAR(32)  NOT NULL,   -- 'Bill' | 'Expense' | 'BillAttachment' | etc.
    [EntityPublicId]    UNIQUEIDENTIFIER NULL,    -- local entity public_id
    [TenantId]          NVARCHAR(64)  NOT NULL,

    -- MS-specific pointers (nullable; populated when relevant)
    [DriveItemId]       NVARCHAR(256) NULL,       -- drive/item that failed (e.g., Excel workbook)
    [WorksheetName]     NVARCHAR(128) NULL,
    [OutboxPublicId]    UNIQUEIDENTIFIER NULL,    -- dead-lettered outbox row (for traceback)

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


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsReconciliationIssue_Status' AND object_id = OBJECT_ID('ms.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_MsReconciliationIssue_Status
        ON [ms].[ReconciliationIssue] ([Status], [Severity])
        WHERE [Action] = 'flagged';
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsReconciliationIssue_Entity' AND object_id = OBJECT_ID('ms.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_MsReconciliationIssue_Entity
        ON [ms].[ReconciliationIssue] ([EntityType], [EntityPublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsReconciliationIssue_Run' AND object_id = OBJECT_ID('ms.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_MsReconciliationIssue_Run
        ON [ms].[ReconciliationIssue] ([ReconcileRunId]);
END
GO


CREATE OR ALTER PROCEDURE CreateMsReconciliationIssue
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
    SET NOCOUNT ON;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [ms].[ReconciliationIssue] (
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


CREATE OR ALTER PROCEDURE ReadMsReconciliationIssuesByStatus
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
    FROM [ms].[ReconciliationIssue]
    WHERE [Status] = @Status
    ORDER BY [CreatedDatetime] DESC;
END;
GO


CREATE OR ALTER PROCEDURE CountMsReconciliationIssues
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [DriftType], [Severity], [Action], [Status],
        COUNT(*) AS [Count]
    FROM [ms].[ReconciliationIssue]
    GROUP BY [DriftType], [Severity], [Action], [Status]
    ORDER BY [DriftType], [Severity];
END;
GO
