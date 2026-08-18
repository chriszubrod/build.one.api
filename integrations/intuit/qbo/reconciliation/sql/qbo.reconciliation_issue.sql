-- ============================================================================
-- qbo.ReconciliationIssue — record of drift between local DB and QBO.
--
-- VERIFIED vs PROD 2026-08-18 (U-249): all 8 sprocs this file defined at that point
-- were diffed against prod sys.sql_modules (normalizing CREATE OR ALTER -> CREATE)
-- and were byte-identical. The earlier "not verified, may be stale" warning is
-- retired — the table/index blocks are IF NOT EXISTS guarded and every sproc uses
-- CREATE OR ALTER, so a whole-file re-apply is idempotent.
--
-- ⚠️ UNAPPLIED AS OF 2026-08-18: the U-249 changes below are NOT yet in prod —
--    (1) BulkResolveQboReconciliationIssuesByFilter gained @Severity, @Action and
--        @KeepNewestPerGroup;  (2) BulkAcknowledgeQboReconciliationIssuesByFilter
--        is new. The repo layer sends params BY NAME, so calling either against the
--        current prod definition fails hard (SQL 8145 / "not a parameter for
--        procedure"). APPLY THIS FILE FIRST, then run the CLI.
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


-- U-249 GAP 2: @Severity / @Action narrowing filters + @KeepNewestPerGroup.
--
-- ---------------------------------------------------------------------------
-- THE GROUPING KEY (load-bearing — "newest per WHAT" is stated here, not implied)
--
--   (RealmId, DriftType, EntityType, QboId, EntityPublicId, Severity, Action)
--
-- "Newest" = MAX(CreatedDatetime), tie-broken by MAX(Id) so "exactly one
-- survivor per group" is deterministic even when two rows share a millisecond
-- (DATETIME2(3) collisions are real: the 20 qbo_voided Expense rows were all
-- emitted inside a 28-second window).
--
-- WHY THESE COLUMNS:
--   * RealmId/DriftType/EntityType/QboId/EntityPublicId = the IDENTITY of the
--     underlying condition. Including QboId is the safety property that makes
--     this feature non-destructive: a per-entity finding (e.g. qbo_voided, which
--     carries a distinct QboId per voided doc) lands ALONE in its own group, so
--     keep-newest is a provable NO-OP on it and cannot collapse 26 real drift
--     rows into 1. Only rows that are literally about the same thing collapse.
--   * Severity/Action = the CLASSIFICATION. Included so keep-newest can never
--     resolve a 'critical'/'manual_review' row in favour of a newer
--     'low'/'flagged' one about the same entity. Measured on live data: 210
--     identity groups span more than one Severity/Action, so this is load-bearing,
--     not decorative. (On today's open backlog it changes nothing — identity
--     groups and full groups are equal — so it costs zero on the target workload.)
--
-- DELIBERATELY EXCLUDED:
--   * ReconcileRunId and CreatedDatetime — these are exactly what VARIES between
--     repeats of the same finding. Including either would make every row its own
--     group and turn keep-newest into a no-op, defeating the whole feature.
--   * Status — lifecycle position, and the caller already scopes it via @Status.
--   * Details — free NVARCHAR(MAX) narrative; varies per run.
--
-- SCOPE OF "NEWEST": the survivor is chosen WITHIN the caller's filtered set,
-- never outside it. The sproc never reaches past the filters the operator stated.
-- Consequence: if a @CreatedBefore cutoff splits a group, the newest row BELOW
-- the cutoff is kept and rows above the cutoff were never candidates — so more
-- than one row may survive. That errs toward keeping signal, which is the correct
-- direction to be wrong in, and a re-run without the cutoff collapses it.
-- ---------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE BulkResolveQboReconciliationIssuesByFilter
(
    @DriftType           NVARCHAR(32)  = NULL,
    @EntityType          NVARCHAR(32)  = NULL,
    @CreatedBefore       DATETIME2(3)  = NULL,
    @RealmId             NVARCHAR(64)  = NULL,
    @Severity            NVARCHAR(16)  = NULL,
    @Action              NVARCHAR(16)  = NULL,
    @Status              NVARCHAR(16)  = 'open',
    @MaxRows             INT           = 1000,
    @KeepNewestPerGroup  BIT           = 0,
    @DryRun              BIT           = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @MaxRows > 5000 SET @MaxRows = 5000;
    IF @MaxRows < 1 SET @MaxRows = 1;

    -- @Severity/@Action are NARROWING-ONLY: they deliberately do NOT satisfy this
    -- guard. '@Severity = low' alone would match every low-severity row of every
    -- drift type in the table — far too broad to be a blast-radius bound.
    IF @DriftType IS NULL AND @EntityType IS NULL AND @CreatedBefore IS NULL
    BEGIN
        RAISERROR('BulkResolveQboReconciliationIssuesByFilter requires at least one of @DriftType, @EntityType, @CreatedBefore', 16, 1);
        RETURN;
    END;

    -- Single definition of "eligible" — both the dry-run preview and the real
    -- resolve below operate on exactly this materialized candidate set, so they
    -- can never see a different row set from each other.
    -- #Ranked holds the full filtered set WITH its per-group recency rank;
    -- #Candidates is #Ranked minus the survivors. Deriving one from the other
    -- keeps the filter predicate written exactly once.
    SELECT [Id], [RecencyRank]
    INTO #Ranked
    FROM (
        SELECT [Id],
               ROW_NUMBER() OVER (
                   PARTITION BY [RealmId], [DriftType], [EntityType],
                                [QboId], [EntityPublicId], [Severity], [Action]
                   ORDER BY [CreatedDatetime] DESC, [Id] DESC
               ) AS [RecencyRank]
        FROM [qbo].[ReconciliationIssue]
        WHERE [Status] = @Status
          AND [Status] IN ('open', 'acknowledged')
          AND (@DriftType IS NULL OR [DriftType] = @DriftType)
          AND (@EntityType IS NULL OR [EntityType] = @EntityType)
          AND (@CreatedBefore IS NULL OR [CreatedDatetime] < @CreatedBefore)
          AND (@RealmId IS NULL OR [RealmId] = @RealmId)
          AND (@Severity IS NULL OR [Severity] = @Severity)
          AND (@Action IS NULL OR [Action] = @Action)
    ) ranked;

    -- RecencyRank = 1 is the newest row of its group. With @KeepNewestPerGroup = 1
    -- it is withheld from the candidate set, so exactly one row per group survives.
    SELECT [Id]
    INTO #Candidates
    FROM #Ranked
    WHERE @KeepNewestPerGroup = 0 OR [RecencyRank] > 1;

    IF @DryRun = 1
    BEGIN
        DECLARE @TotalMatchCount INT = (SELECT COUNT(*) FROM #Candidates);
        -- Rows the filter matched but keep-newest is withholding = one per group.
        DECLARE @TotalKeptCount INT =
            (SELECT COUNT(*) FROM #Ranked) - @TotalMatchCount;

        SELECT TOP (10)
            ri.[Id], ri.[DriftType], ri.[EntityType], ri.[QboId],
            ri.[Severity], ri.[Action],
            CONVERT(VARCHAR(19), ri.[CreatedDatetime], 120) AS [CreatedDatetime],
            @TotalMatchCount AS [TotalMatchCount],
            @TotalKeptCount  AS [TotalKeptCount]
        FROM [qbo].[ReconciliationIssue] ri
        JOIN #Candidates c ON c.[Id] = ri.[Id]
        ORDER BY ri.[CreatedDatetime] ASC;

        DROP TABLE #Candidates;
        DROP TABLE #Ranked;
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
    DROP TABLE #Ranked;
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


-- ========================= U-249 BEGIN =========================
-- GAP 1: bulk ACKNOWLEDGE. Acknowledgement and resolution are different verbs and
-- must not be conflated:
--   acknowledged = "a human has SEEN this; it is real and still awaiting action"
--   resolved     = "this has been DEALT WITH"
-- The qbo_voided backlog (Bill + Expense) is real, per-entity drift — one row per
-- genuinely voided QBO document. Resolving it would erase live signal; the correct
-- transition is open -> acknowledged, in bulk, instead of N single-row calls.
--
-- Shape, guards, clamps, dry-run contract and concurrency re-check are mirrored
-- from BulkResolveQboReconciliationIssuesByFilter deliberately, so an operator who
-- knows one knows the other.
--
-- NO @KeepNewestPerGroup here, by design. Keep-newest exists to thin RECURRING
-- summary rows that all describe one condition; acknowledgement is applied to
-- per-entity findings where every row is a distinct real item and must be seen.
-- Thinning them would defeat the point of acknowledging them at all.
CREATE OR ALTER PROCEDURE BulkAcknowledgeQboReconciliationIssuesByFilter
(
    @DriftType       NVARCHAR(32)  = NULL,
    @EntityType      NVARCHAR(32)  = NULL,
    @CreatedBefore   DATETIME2(3)  = NULL,
    @RealmId         NVARCHAR(64)  = NULL,
    @Severity        NVARCHAR(16)  = NULL,
    @Action          NVARCHAR(16)  = NULL,
    @Status          NVARCHAR(16)  = 'open',
    @MaxRows         INT           = 1000,
    @DryRun          BIT           = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @MaxRows > 5000 SET @MaxRows = 5000;
    IF @MaxRows < 1 SET @MaxRows = 1;

    -- Same blast-radius bound as bulk-resolve: @Severity/@Action narrow, they do
    -- not authorise a sweep on their own.
    IF @DriftType IS NULL AND @EntityType IS NULL AND @CreatedBefore IS NULL
    BEGIN
        RAISERROR('BulkAcknowledgeQboReconciliationIssuesByFilter requires at least one of @DriftType, @EntityType, @CreatedBefore', 16, 1);
        RETURN;
    END;

    -- Single definition of "eligible", shared by the dry-run preview and the real
    -- acknowledge below. Note the status predicate is intersected with 'open':
    -- open is the ONLY legal source state for this transition (mirrors the
    -- single-row AcknowledgeQboReconciliationIssue). A caller passing any other
    -- @Status therefore selects nothing and the call is a safe no-op rather than
    -- an illegal backwards transition from 'resolved'.
    SELECT [Id]
    INTO #AckCandidates
    FROM [qbo].[ReconciliationIssue]
    WHERE [Status] = @Status
      AND [Status] = 'open'
      AND (@DriftType IS NULL OR [DriftType] = @DriftType)
      AND (@EntityType IS NULL OR [EntityType] = @EntityType)
      AND (@CreatedBefore IS NULL OR [CreatedDatetime] < @CreatedBefore)
      AND (@RealmId IS NULL OR [RealmId] = @RealmId)
      AND (@Severity IS NULL OR [Severity] = @Severity)
      AND (@Action IS NULL OR [Action] = @Action);

    IF @DryRun = 1
    BEGIN
        DECLARE @TotalMatchCount INT = (SELECT COUNT(*) FROM #AckCandidates);

        SELECT TOP (10)
            ri.[Id], ri.[DriftType], ri.[EntityType], ri.[QboId],
            ri.[Severity], ri.[Action],
            CONVERT(VARCHAR(19), ri.[CreatedDatetime], 120) AS [CreatedDatetime],
            @TotalMatchCount AS [TotalMatchCount]
        FROM [qbo].[ReconciliationIssue] ri
        JOIN #AckCandidates c ON c.[Id] = ri.[Id]
        ORDER BY ri.[CreatedDatetime] ASC;

        DROP TABLE #AckCandidates;
        RETURN;
    END;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    ;WITH [Batch] AS (
        SELECT TOP (@MaxRows) c.[Id]
        FROM #AckCandidates c
        JOIN [qbo].[ReconciliationIssue] ri ON ri.[Id] = c.[Id]
        ORDER BY ri.[CreatedDatetime] ASC
    )
    -- #AckCandidates is a snapshot taken before this transaction opened — re-check
    -- Status here so a row acknowledged or resolved by a concurrent call between
    -- the snapshot and this UPDATE is excluded rather than having its
    -- AcknowledgedAt clobbered (or being dragged back out of 'resolved').
    UPDATE ri
    SET [Status] = 'acknowledged',
        [AcknowledgedAt] = @Now,
        [ModifiedDatetime] = @Now
    OUTPUT INSERTED.[Id]
    FROM [qbo].[ReconciliationIssue] ri
    JOIN [Batch] b ON b.[Id] = ri.[Id]
    WHERE ri.[Status] = 'open';

    COMMIT TRANSACTION;

    DROP TABLE #AckCandidates;
END;
GO
-- ========================== U-249 END ==========================
