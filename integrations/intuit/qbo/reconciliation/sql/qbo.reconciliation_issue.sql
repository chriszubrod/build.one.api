-- ============================================================================
-- qbo.ReconciliationIssue — record of drift between local DB and QBO.
--
-- WARNING: this base file has NOT been verified against prod (sys.sql_modules)
-- and may be stale — do NOT re-apply it wholesale. The U-160 sproc at the bottom
-- is wrapped in a BEGIN/END banner so it can be extracted and applied on its own.
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


-- ========================= U-160 BEGIN =========================
-- Dedupe key-source for the qbo_voided void detectors: narrow 3-column projection
-- deliberately avoiding Details NVARCHAR(MAX). Status <> 'resolved' is the
-- "unresolved" test (open + acknowledged both suppress; resolved does not).
CREATE OR ALTER PROCEDURE ReadQboUnresolvedIssueKeysByDriftType
(
    @DriftType NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT [RealmId], [EntityType], [QboId]
    FROM [qbo].[ReconciliationIssue]
    WHERE [DriftType] = @DriftType
      AND [Action] = 'flagged'
      AND [Status] <> 'resolved'
      AND [QboId] IS NOT NULL;
END;
GO
-- ========================== U-160 END ==========================


-- ========================= U-246 BEGIN =========================

CREATE OR ALTER PROCEDURE AcknowledgeQboReconciliationIssue
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[ReconciliationIssue]
    SET [Status] = 'acknowledged',
        [AcknowledgedAt] = @Now,
        [ModifiedDatetime] = @Now
    WHERE [Id] = @Id
      AND [Status] = 'open';

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
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ResolveQboReconciliationIssue
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[ReconciliationIssue]
    SET [Status] = 'resolved',
        [ResolvedAt] = @Now,
        [ModifiedDatetime] = @Now
    WHERE [Id] = @Id
      AND [Status] IN ('open', 'acknowledged');

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
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE BulkResolveQboReconciliationIssuesByFilter
(
    @DriftType       NVARCHAR(32)  = NULL,
    @EntityType      NVARCHAR(32)  = NULL,
    @CreatedBefore   DATETIME2(3)  = NULL,
    @RealmId         NVARCHAR(64)  = NULL,
    @Status          NVARCHAR(16)  = 'open',
    @MaxRows         INT           = 1000,
    @DryRun          BIT           = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @MaxRows > 5000 SET @MaxRows = 5000;
    IF @MaxRows < 1 SET @MaxRows = 1;

    IF @DriftType IS NULL AND @EntityType IS NULL AND @CreatedBefore IS NULL
    BEGIN
        RAISERROR('BulkResolveQboReconciliationIssuesByFilter requires at least one of @DriftType, @EntityType, @CreatedBefore', 16, 1);
        RETURN;
    END;

    -- Single definition of "eligible" — both the dry-run preview and the real
    -- resolve below operate on exactly this materialized candidate set, so they
    -- can never see a different row set from each other.
    SELECT [Id]
    INTO #Candidates
    FROM [qbo].[ReconciliationIssue]
    WHERE [Status] = @Status
      AND [Status] IN ('open', 'acknowledged')
      AND (@DriftType IS NULL OR [DriftType] = @DriftType)
      AND (@EntityType IS NULL OR [EntityType] = @EntityType)
      AND (@CreatedBefore IS NULL OR [CreatedDatetime] < @CreatedBefore)
      AND (@RealmId IS NULL OR [RealmId] = @RealmId);

    IF @DryRun = 1
    BEGIN
        DECLARE @TotalMatchCount INT = (SELECT COUNT(*) FROM #Candidates);

        SELECT TOP (10)
            ri.[Id], ri.[DriftType], ri.[EntityType], ri.[QboId],
            CONVERT(VARCHAR(19), ri.[CreatedDatetime], 120) AS [CreatedDatetime],
            @TotalMatchCount AS [TotalMatchCount]
        FROM [qbo].[ReconciliationIssue] ri
        JOIN #Candidates c ON c.[Id] = ri.[Id]
        ORDER BY ri.[CreatedDatetime] ASC;

        DROP TABLE #Candidates;
        RETURN;
    END;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    ;WITH [Batch] AS (
        SELECT TOP (@MaxRows) c.[Id]
        FROM #Candidates c
        JOIN [qbo].[ReconciliationIssue] ri ON ri.[Id] = c.[Id]
        ORDER BY ri.[CreatedDatetime] ASC
    )
    -- #Candidates is a snapshot taken before this transaction opened — re-check
    -- Status here so a row resolved by a concurrent call between the snapshot
    -- and this UPDATE is excluded rather than having its ResolvedAt clobbered.
    UPDATE ri
    SET [Status] = 'resolved',
        [ResolvedAt] = @Now,
        [ModifiedDatetime] = @Now
    OUTPUT INSERTED.[Id]
    FROM [qbo].[ReconciliationIssue] ri
    JOIN [Batch] b ON b.[Id] = ri.[Id]
    WHERE ri.[Status] IN ('open', 'acknowledged');

    COMMIT TRANSACTION;

    DROP TABLE #Candidates;
END;
GO


CREATE OR ALTER PROCEDURE ReadQboReconciliationIssueTriageSummary
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [DriftType],
        [EntityType],
        [Severity],
        [Action],
        [Status],
        COUNT(*) AS [RowCount],
        COUNT(DISTINCT CASE WHEN [QboId] IS NOT NULL
            THEN CONCAT([EntityType], '|', [QboId]) END) AS [UniqueKeyCount],
        MIN([CreatedDatetime]) AS [FirstSeen],
        MAX([CreatedDatetime]) AS [LastSeen]
    FROM [qbo].[ReconciliationIssue]
    GROUP BY [DriftType], [EntityType], [Severity], [Action], [Status]
    ORDER BY [RowCount] DESC;
END;
GO

-- ========================== U-246 END ==========================
