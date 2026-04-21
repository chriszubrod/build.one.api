-- ============================================================================
-- qbo.ReconciliationIssue — record of drift between local DB and QBO.
--
-- Written by the reconciliation job when it detects a mismatch between
-- what QBO says and what we have locally. Each row represents one finding.
--
-- Auto-fixable drift is applied immediately and the row is written with
-- `action = 'auto_fixed'` for the audit trail. Drift that requires human
-- judgment lands with `action = 'flagged'` and `status = 'open'` — a small
-- review UI (future task) lets the operator acknowledge or resolve it.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'qbo')
    EXEC('CREATE SCHEMA qbo AUTHORIZATION dbo;');
GO


IF OBJECT_ID('qbo.ReconciliationIssue', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[ReconciliationIssue]
(
    [Id]                BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION NOT NULL,
    [CreatedDatetime]   DATETIME2(3) NOT NULL,
    [ModifiedDatetime]  DATETIME2(3) NULL,

    -- What drift was found
    [DriftType]         NVARCHAR(32)  NOT NULL,   -- 'qbo_missing_locally' | 'local_missing_qbo' |
                                                    -- 'stale_sync_token' | 'missing_mapping' |
                                                    -- 'field_mismatch' | 'duplicate_mapping' | 'qbo_voided'
    [Severity]          NVARCHAR(16)  NOT NULL,   -- 'low' | 'medium' | 'high'
    [Action]            NVARCHAR(16)  NOT NULL,   -- 'auto_fixed' | 'flagged'

    -- What entity the drift relates to
    [EntityType]        NVARCHAR(32)  NOT NULL,   -- 'Bill' | 'Invoice' | 'Purchase' | 'VendorCredit'
    [EntityPublicId]    UNIQUEIDENTIFIER NULL,    -- local entity public_id (NULL if drift is purely QBO-side)
    [QboId]             NVARCHAR(64)  NULL,       -- QBO entity id (NULL for local-only drift)
    [RealmId]           NVARCHAR(64)  NOT NULL,

    -- Diagnostic text — human readable
    [Details]           NVARCHAR(MAX) NULL,

    -- Review lifecycle
    [Status]            NVARCHAR(16)  NOT NULL DEFAULT 'open',
                                                    -- 'open' | 'acknowledged' | 'resolved'
    [AcknowledgedAt]    DATETIME2(3)  NULL,
    [ResolvedAt]        DATETIME2(3)  NULL,

    -- Run correlation
    [ReconcileRunId]    UNIQUEIDENTIFIER NULL      -- groups issues from the same reconciliation run
);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReconciliationIssue_Status' AND object_id = OBJECT_ID('qbo.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_ReconciliationIssue_Status
        ON [qbo].[ReconciliationIssue] ([Status], [Severity])
        WHERE [Action] = 'flagged';
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReconciliationIssue_Entity' AND object_id = OBJECT_ID('qbo.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_ReconciliationIssue_Entity
        ON [qbo].[ReconciliationIssue] ([EntityType], [EntityPublicId]);
END
GO


IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReconciliationIssue_Run' AND object_id = OBJECT_ID('qbo.ReconciliationIssue'))
BEGIN
    CREATE INDEX IX_ReconciliationIssue_Run
        ON [qbo].[ReconciliationIssue] ([ReconcileRunId]);
END
GO


CREATE OR ALTER PROCEDURE CreateQboReconciliationIssue
(
    @DriftType       NVARCHAR(32),
    @Severity        NVARCHAR(16),
    @Action          NVARCHAR(16),
    @EntityType      NVARCHAR(32),
    @EntityPublicId  UNIQUEIDENTIFIER = NULL,
    @QboId           NVARCHAR(64) = NULL,
    @RealmId         NVARCHAR(64),
    @Details         NVARCHAR(MAX) = NULL,
    @ReconcileRunId  UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[ReconciliationIssue] (
        [CreatedDatetime], [ModifiedDatetime],
        [DriftType], [Severity], [Action],
        [EntityType], [EntityPublicId], [QboId], [RealmId],
        [Details], [Status], [ReconcileRunId]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[DriftType], INSERTED.[Severity], INSERTED.[Action],
        INSERTED.[EntityType], INSERTED.[EntityPublicId], INSERTED.[QboId], INSERTED.[RealmId],
        INSERTED.[Details], INSERTED.[Status], INSERTED.[ReconcileRunId],
        CONVERT(VARCHAR(19), INSERTED.[AcknowledgedAt], 120) AS [AcknowledgedAt],
        CONVERT(VARCHAR(19), INSERTED.[ResolvedAt], 120) AS [ResolvedAt]
    VALUES (
        @Now, NULL,
        @DriftType, @Severity, @Action,
        @EntityType, @EntityPublicId, @QboId, @RealmId,
        @Details, 'open', @ReconcileRunId
    );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadQboReconciliationIssuesByStatus
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
        [EntityType], [EntityPublicId], [QboId], [RealmId],
        [Details], [Status], [ReconcileRunId],
        CONVERT(VARCHAR(19), [AcknowledgedAt], 120) AS [AcknowledgedAt],
        CONVERT(VARCHAR(19), [ResolvedAt], 120) AS [ResolvedAt]
    FROM [qbo].[ReconciliationIssue]
    WHERE [Status] = @Status
    ORDER BY [CreatedDatetime] DESC;
END;
GO


CREATE OR ALTER PROCEDURE CountQboReconciliationIssues
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [DriftType],
        [Severity],
        [Action],
        [Status],
        COUNT(*) AS [Count]
    FROM [qbo].[ReconciliationIssue]
    GROUP BY [DriftType], [Severity], [Action], [Status]
    ORDER BY [DriftType], [Severity];
END;
GO
