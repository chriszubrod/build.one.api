-- Contract: dbo.ContractLaborCodingSuggestion
-- Owns: dbo.ContractLaborCodingSuggestion (U-797a shadow predictions, one row per crew-member day per project); ids, codes and numbers only.
-- Invariants: unique (ContractLaborId, ProjectId); no note or reply text is stored; mutation sprocs SET NOCOUNT ON and take no in-proc transaction control (pyodbc autocommit-off); Enqueue/Claim/Complete/Release are the only writers; the 24 h stale sweep in Claim guards staleness only; the leak guard is the tick's own_cl_coded skip (a row whose own ContractLabor is labelled is never predicted).
-- Sprocs: EnqueueContractLaborCodingSuggestions, ClaimNextContractLaborCodingSuggestion, ReadContractLaborCodingShadowInputs, CompleteContractLaborCodingSuggestion, ReleaseContractLaborCodingSuggestion, CountContractLaborCodingCallsToday, ReadContractLaborCodingScorecardRows, ReadContractLaborCodingIdfCorpus
-- ─────────────────────────────────────────────────────────────────────
-- U-797a: Contract Labor coding suggestion (shadow). Stores one suggested SubCostCode
-- (or an abstain) and a split flag per (ContractLabor, Project), then the scorecard reads
-- the PM's final codes back at read time. Nothing a person sees changes (shadow only).
-- Status: pending -> done | failed | skipped. ModelVersion u797a-cl-v1.
-- ─────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.ContractLaborCodingSuggestion', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ContractLaborCodingSuggestion]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT GETUTCDATE(),
    [ModifiedDatetime] DATETIME2(3) NULL,

    [ContractLaborId] BIGINT NOT NULL,
    [ProjectId] BIGINT NOT NULL,
    [WorkDate] DATE NOT NULL,
    [VendorId] BIGINT NULL,

    [Status] NVARCHAR(20) NOT NULL CONSTRAINT [DF_ContractLaborCodingSuggestion_Status] DEFAULT N'pending',
    [ModelVersion] NVARCHAR(50) NOT NULL CONSTRAINT [DF_ContractLaborCodingSuggestion_ModelVersion] DEFAULT N'u797a-cl-v1',

    -- The suggestion
    [DecisionSource] NVARCHAR(20) NULL,
    [SubCostCodeId] BIGINT NULL,
    [Confidence] DECIMAL(5,4) NULL,
    [MaySplit] BIT NULL,
    [ModelProposal] NVARCHAR(10) NULL,
    [ModelSplitCodes] NVARCHAR(200) NULL,

    -- Rung inputs
    [HistoryTopSubCostCodeId] BIGINT NULL,
    [HistoryShare] DECIMAL(5,4) NULL,
    [HistoryPriorCount] INT NULL,
    [ArmACodeCount] INT NULL,

    -- Call record (ids, codes and numbers only)
    [Model] NVARCHAR(100) NULL,
    [FailureReason] NVARCHAR(100) NULL,
    [ClaimedDatetime] DATETIME2(3) NULL,
    [CallDatetime] DATETIME2(3) NULL,
    [AttemptCount] INT NOT NULL CONSTRAINT [DF_ContractLaborCodingSuggestion_AttemptCount] DEFAULT 0,

    CONSTRAINT [FK_ContractLaborCodingSuggestion_ContractLabor] FOREIGN KEY ([ContractLaborId]) REFERENCES [dbo].[ContractLabor]([Id]),
    CONSTRAINT [FK_ContractLaborCodingSuggestion_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [FK_ContractLaborCodingSuggestion_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [CK_ContractLaborCodingSuggestion_Status] CHECK ([Status] IN (N'pending', N'done', N'failed', N'skipped')),
    CONSTRAINT [CK_ContractLaborCodingSuggestion_DecisionSource] CHECK ([DecisionSource] IS NULL OR [DecisionSource] IN (N'history', N'model', N'none')),
    CONSTRAINT [CK_ContractLaborCodingSuggestion_ModelProposal] CHECK ([ModelProposal] IS NULL OR [ModelProposal] IN (N'single', N'split', N'abstain'))
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_ContractLaborCodingSuggestion_CL_Project' AND object_id = OBJECT_ID('dbo.ContractLaborCodingSuggestion'))
BEGIN
CREATE UNIQUE NONCLUSTERED INDEX [UX_ContractLaborCodingSuggestion_CL_Project]
    ON [dbo].[ContractLaborCodingSuggestion] ([ContractLaborId], [ProjectId]);
END
GO

-- The claim and the stale sweep run every 2 minutes; seek the few pending rows, not the whole table.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLaborCodingSuggestion_Pending' AND object_id = OBJECT_ID('dbo.ContractLaborCodingSuggestion'))
BEGIN
CREATE NONCLUSTERED INDEX [IX_ContractLaborCodingSuggestion_Pending]
    ON [dbo].[ContractLaborCodingSuggestion] ([ClaimedDatetime], [CreatedDatetime], [Id])
    WHERE [Status] = N'pending';
END
GO

-- Enqueue one pending row per distinct line ProjectId of the CL (header ProjectId when no line
-- has one). Idempotent via NOT EXISTS. Returns the number inserted.
CREATE OR ALTER PROCEDURE dbo.EnqueueContractLaborCodingSuggestions
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @WorkDate DATE;
    DECLARE @VendorId BIGINT;
    DECLARE @HeaderProjectId BIGINT;

    SELECT
        @WorkDate = cl.[WorkDate],
        @VendorId = COALESCE(cl.[VendorId], cl.[BillVendorId]),
        @HeaderProjectId = cl.[ProjectId]
    FROM dbo.[ContractLabor] cl
    WHERE cl.[Id] = @ContractLaborId;

    DECLARE @Projects TABLE ([ProjectId] BIGINT NOT NULL PRIMARY KEY);

    INSERT INTO @Projects ([ProjectId])
    SELECT DISTINCT li.[ProjectId]
    FROM dbo.[ContractLaborLineItem] li
    WHERE li.[ContractLaborId] = @ContractLaborId
      AND li.[ProjectId] IS NOT NULL;

    IF NOT EXISTS (SELECT 1 FROM @Projects) AND @HeaderProjectId IS NOT NULL
        INSERT INTO @Projects ([ProjectId]) VALUES (@HeaderProjectId);

    INSERT INTO dbo.[ContractLaborCodingSuggestion]
        ([PublicId], [CreatedDatetime], [ContractLaborId], [ProjectId], [WorkDate], [VendorId], [Status], [ModelVersion])
    SELECT NEWID(), GETUTCDATE(), @ContractLaborId, p.[ProjectId], @WorkDate, @VendorId, N'pending', N'u797a-cl-v1'
    FROM @Projects p
    WHERE @WorkDate IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM dbo.[ContractLaborCodingSuggestion] s
          WHERE s.[ContractLaborId] = @ContractLaborId AND s.[ProjectId] = p.[ProjectId]);

    DECLARE @Inserted INT = @@ROWCOUNT;

    SELECT @Inserted AS [InsertedCount];
END
GO

-- Claim the oldest pending row (UPDLOCK + READPAST). Rows pending longer than 24 h are
-- first set to skipped ('stale'). This guards staleness only; it does not prevent leaks. The
-- leak guard is the tick's own_cl_coded skip. A claim is a 15-minute lease; an expired claim
-- can be taken again. Never-claimed rows sort first (ClaimedDatetime IS NULL): a row released after a
-- failed attempt carries a lease, and if it sorted by age alone one repeatedly failing row would hold the
-- head of the queue and starve the never-claimed rows behind it (U-797a P1 starvation).
-- AttemptCount is returned so the service can apply the three-strike rule.
CREATE OR ALTER PROCEDURE dbo.ClaimNextContractLaborCodingSuggestion
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[ContractLaborCodingSuggestion]
    SET [Status] = N'skipped',
        [FailureReason] = N'stale',
        [ModifiedDatetime] = GETUTCDATE()
    WHERE [Status] = N'pending'
      AND [CreatedDatetime] < DATEADD(hour, -24, GETUTCDATE());

    DECLARE @Id BIGINT;

    SELECT TOP (1) @Id = s.[Id]
    FROM dbo.[ContractLaborCodingSuggestion] s WITH (UPDLOCK, READPAST, ROWLOCK)
    WHERE s.[Status] = N'pending'
      AND (s.[ClaimedDatetime] IS NULL OR s.[ClaimedDatetime] < DATEADD(minute, -15, GETUTCDATE()))
    ORDER BY CASE WHEN s.[ClaimedDatetime] IS NULL THEN 0 ELSE 1 END, s.[CreatedDatetime], s.[Id];

    IF @Id IS NOT NULL
        UPDATE dbo.[ContractLaborCodingSuggestion]
        SET [ClaimedDatetime] = GETUTCDATE(),
            [ModifiedDatetime] = GETUTCDATE()
        WHERE [Id] = @Id;

    SELECT
        s.[Id],
        s.[PublicId],
        s.[ContractLaborId],
        s.[ProjectId],
        s.[WorkDate],
        s.[VendorId],
        s.[Status],
        s.[ModelVersion],
        s.[AttemptCount]
    FROM dbo.[ContractLaborCodingSuggestion] s
    WHERE s.[Id] = @Id;
END
GO

-- Read-only inputs for one (ContractLabor, Project) day. Result sets, in order:
--   1. header: ContractLaborId, WorkDate, TotalHours (this project's line hours, else CL total),
--      Status (the row's own ContractLabor status; a labelled status means the tick skips the row)
--   2. the day's TimeLog work notes on this project (ClockIn, Note)
--   3. precedent coded lines on the same project, LineDate within +/- 14 calendar days of
--      WorkDate, this CL excluded, labelled statuses only, holding codes excluded:
--      (ContractLaborId, LineDate, SubCostCodeId, Hours)
--   4. the precedents' TimeLog work notes on this project (ContractLaborId, ClockIn, Note)
--   5. the code catalog of non-holding SubCostCodes (Id, Number, Name)
CREATE OR ALTER PROCEDURE dbo.ReadContractLaborCodingShadowInputs
(
    @ContractLaborId BIGINT,
    @ProjectId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @WorkDate DATE;
    DECLARE @TotalHours DECIMAL(10,2);
    DECLARE @TimeEntryId BIGINT;

    SELECT
        @WorkDate = cl.[WorkDate],
        @TotalHours = COALESCE(
            (SELECT SUM(li.[Hours]) FROM dbo.[ContractLaborLineItem] li
             WHERE li.[ContractLaborId] = cl.[Id] AND li.[ProjectId] = @ProjectId),
            cl.[TotalHours]),
        @TimeEntryId = COALESCE(
            (SELECT TOP (1) li.[SourceTimeEntryId] FROM dbo.[ContractLaborLineItem] li
             WHERE li.[ContractLaborId] = cl.[Id] AND li.[ProjectId] = @ProjectId
               AND li.[SourceTimeEntryId] IS NOT NULL
             ORDER BY li.[Id]),
            cl.[SourceTimeEntryId])
    FROM dbo.[ContractLabor] cl
    WHERE cl.[Id] = @ContractLaborId;

    CREATE TABLE #Precedent
    (
        [ContractLaborId] BIGINT NOT NULL,
        [LineDate] DATE NOT NULL,
        [SubCostCodeId] BIGINT NOT NULL,
        [Hours] DECIMAL(10,2) NULL,
        [TimeEntryId] BIGINT NULL
    );

    IF @WorkDate IS NOT NULL
        INSERT INTO #Precedent ([ContractLaborId], [LineDate], [SubCostCodeId], [Hours], [TimeEntryId])
        SELECT
            li.[ContractLaborId],
            COALESCE(li.[LineDate], cl2.[WorkDate]),
            li.[SubCostCodeId],
            li.[Hours],
            COALESCE(li.[SourceTimeEntryId], cl2.[SourceTimeEntryId])
        FROM dbo.[ContractLaborLineItem] li
        INNER JOIN dbo.[ContractLabor] cl2 ON cl2.[Id] = li.[ContractLaborId]
        INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = li.[SubCostCodeId]
        WHERE cl2.[Status] IN (N'approved', N'completed', N'ready', N'billed')
          AND li.[ProjectId] = @ProjectId
          AND li.[ContractLaborId] <> @ContractLaborId
          AND li.[SubCostCodeId] IS NOT NULL
          AND ISNULL(scc.[IsHoldingCode], 0) = 0
          AND COALESCE(li.[LineDate], cl2.[WorkDate]) <> @WorkDate
          AND COALESCE(li.[LineDate], cl2.[WorkDate])
              BETWEEN DATEADD(day, -14, @WorkDate) AND DATEADD(day, 14, @WorkDate);

    -- 1. header
    SELECT cl.[Id] AS [ContractLaborId], cl.[WorkDate], @TotalHours AS [TotalHours], cl.[Status]
    FROM dbo.[ContractLabor] cl
    WHERE cl.[Id] = @ContractLaborId;

    -- 2. the day's work notes
    SELECT tl.[ClockIn], tl.[Note]
    FROM dbo.[TimeLog] tl
    WHERE tl.[TimeEntryId] = @TimeEntryId
      AND tl.[ProjectId] = @ProjectId
      AND (tl.[LogType] IS NULL OR tl.[LogType] = N'work')
      AND tl.[Note] IS NOT NULL
    ORDER BY tl.[ClockIn];

    -- 3. precedent lines
    SELECT p.[ContractLaborId], p.[LineDate], p.[SubCostCodeId], p.[Hours]
    FROM #Precedent p
    ORDER BY p.[LineDate], p.[ContractLaborId], p.[SubCostCodeId];

    -- 4. precedent notes
    SELECT DISTINCT p.[ContractLaborId], tl.[ClockIn], tl.[Note]
    FROM #Precedent p
    INNER JOIN dbo.[TimeLog] tl ON tl.[TimeEntryId] = p.[TimeEntryId] AND tl.[ProjectId] = @ProjectId
    WHERE p.[TimeEntryId] IS NOT NULL
      AND (tl.[LogType] IS NULL OR tl.[LogType] = N'work')
      AND tl.[Note] IS NOT NULL
    ORDER BY p.[ContractLaborId], tl.[ClockIn];

    -- 5. catalog
    SELECT scc.[Id], scc.[Number], scc.[Name]
    FROM dbo.[SubCostCode] scc
    WHERE ISNULL(scc.[IsHoldingCode], 0) = 0;

    DROP TABLE #Precedent;
END
GO

-- Final write for one suggestion. @Model is set only when a model call was attempted, which
-- is what the daily call cap counts. The Python layer decides the values.
CREATE OR ALTER PROCEDURE dbo.CompleteContractLaborCodingSuggestion
(
    @Id BIGINT,
    @Status NVARCHAR(20),
    @DecisionSource NVARCHAR(20) = NULL,
    @SubCostCodeId BIGINT = NULL,
    @Confidence DECIMAL(5,4) = NULL,
    @MaySplit BIT = NULL,
    @ModelProposal NVARCHAR(10) = NULL,
    @ModelSplitCodes NVARCHAR(200) = NULL,
    @HistoryTopSubCostCodeId BIGINT = NULL,
    @HistoryShare DECIMAL(5,4) = NULL,
    @HistoryPriorCount INT = NULL,
    @ArmACodeCount INT = NULL,
    @Model NVARCHAR(100) = NULL,
    @FailureReason NVARCHAR(100) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[ContractLaborCodingSuggestion]
    SET [Status] = @Status,
        [DecisionSource] = @DecisionSource,
        [SubCostCodeId] = @SubCostCodeId,
        [Confidence] = @Confidence,
        [MaySplit] = @MaySplit,
        [ModelProposal] = @ModelProposal,
        [ModelSplitCodes] = @ModelSplitCodes,
        [HistoryTopSubCostCodeId] = @HistoryTopSubCostCodeId,
        [HistoryShare] = @HistoryShare,
        [HistoryPriorCount] = @HistoryPriorCount,
        [ArmACodeCount] = @ArmACodeCount,
        [Model] = @Model,
        [FailureReason] = @FailureReason,
        [CallDatetime] = CASE WHEN @Model IS NOT NULL THEN GETUTCDATE() ELSE [CallDatetime] END,
        [ClaimedDatetime] = NULL,
        [ModifiedDatetime] = GETUTCDATE()
    WHERE [Id] = @Id;

    SELECT s.[Id], s.[Status], s.[DecisionSource], s.[SubCostCodeId], s.[MaySplit]
    FROM dbo.[ContractLaborCodingSuggestion] s
    WHERE s.[Id] = @Id;
END
GO

-- Return a claimed row to pending, with a fresh 15-minute lease. The lease stops the tick from
-- re-claiming the same row straight away. Status unchanged.
-- @CountAttempt = 1 records a failed model attempt (infrastructure or row fault): AttemptCount +1 and
-- CallDatetime = now, so the attempt counts against today's call cap and the three-strike rule. Policy
-- deferrals (cap, stopped, headroom) pass 0: no attempt was made.
CREATE OR ALTER PROCEDURE dbo.ReleaseContractLaborCodingSuggestion
(
    @Id BIGINT,
    @CountAttempt BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[ContractLaborCodingSuggestion]
    SET [ClaimedDatetime] = SYSUTCDATETIME(),
        [AttemptCount] = CASE WHEN @CountAttempt = 1 THEN [AttemptCount] + 1 ELSE [AttemptCount] END,
        [CallDatetime] = CASE WHEN @CountAttempt = 1 THEN SYSUTCDATETIME() ELSE [CallDatetime] END,
        [ModifiedDatetime] = GETUTCDATE()
    WHERE [Id] = @Id
      AND [Status] = N'pending';

    SELECT s.[Id], s.[Status]
    FROM dbo.[ContractLaborCodingSuggestion] s
    WHERE s.[Id] = @Id;
END
GO

-- Model attempts made today (UTC day, the same boundary as the Claude daily cap). A row counts when its
-- CallDatetime is today and it either completed with a model call (Model set) or had a failed attempt
-- released (AttemptCount > 0). A row counts ONCE per day even when it took two attempts, which is bounded
-- by the three-strike rule. Pre-model infrastructure failures also count, as attempts.
CREATE OR ALTER PROCEDURE dbo.CountContractLaborCodingCallsToday
AS
BEGIN
    SET NOCOUNT ON;

    SELECT COUNT(*) AS [CallCount]
    FROM dbo.[ContractLaborCodingSuggestion]
    WHERE ([Model] IS NOT NULL OR [AttemptCount] > 0)
      AND [CallDatetime] >= CAST(CAST(GETUTCDATE() AS DATE) AS DATETIME2(3));
END
GO

-- Broad labelled corpus for the arm-A IDF (U-750 backtest: one document per labelled line, all
-- projects and dates). Rows are the day's TimeLog work notes on the line's project, keyed by
-- ContractLaborLineItem.Id. Python rebuilds each line's note text. Labelled = the U-673 status
-- set, holding codes excluded, TimeLog-sourced. No Hours filter: this matches U-750/U-673 _LINES_SQL,
-- which takes every labelled line (zero-hour lines included), so the IDF corpus is the same corpus.
CREATE OR ALTER PROCEDURE dbo.ReadContractLaborCodingIdfCorpus
AS
BEGIN
    SET NOCOUNT ON;

    SELECT li.[Id] AS [LineId], tl.[ClockIn], tl.[Note]
    FROM dbo.[ContractLaborLineItem] li
    INNER JOIN dbo.[ContractLabor] cl ON cl.[Id] = li.[ContractLaborId]
    INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = li.[SubCostCodeId]
    INNER JOIN dbo.[TimeLog] tl
        ON tl.[TimeEntryId] = COALESCE(li.[SourceTimeEntryId], cl.[SourceTimeEntryId])
       AND tl.[ProjectId] = li.[ProjectId]
    WHERE cl.[Status] IN (N'approved', N'completed', N'ready', N'billed')
      AND li.[SubCostCodeId] IS NOT NULL
      AND ISNULL(scc.[IsHoldingCode], 0) = 0
      AND (tl.[LogType] IS NULL OR tl.[LogType] = N'work')
      AND tl.[Note] IS NOT NULL
    ORDER BY li.[Id], tl.[ClockIn];
END
GO

-- Scorecard rows: every done suggestion, joined to its CL. FinalCodeIds is the distinct
-- non-holding line codes with Hours > 0 on that (ContractLabor, Project), comma-separated, read at
-- read time and only for CLs in a labelled status (approved/completed/ready/billed). NULL = not
-- finalized. FailureReason lets the scorecard exclude availability failures from every rate.
CREATE OR ALTER PROCEDURE dbo.ReadContractLaborCodingScorecardRows
(
    @Since DATE = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        s.[Id],
        s.[ContractLaborId],
        s.[ProjectId],
        s.[WorkDate],
        s.[DecisionSource],
        s.[SubCostCodeId],
        s.[Confidence],
        s.[MaySplit],
        s.[FailureReason],
        CASE WHEN cl.[Status] IN (N'approved', N'completed', N'ready', N'billed')
             THEN (
                 SELECT STRING_AGG(CAST(x.[SubCostCodeId] AS NVARCHAR(20)), N',')
                 FROM (
                     SELECT DISTINCT li.[SubCostCodeId]
                     FROM dbo.[ContractLaborLineItem] li
                     INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = li.[SubCostCodeId]
                     WHERE li.[ContractLaborId] = s.[ContractLaborId]
                       AND li.[ProjectId] = s.[ProjectId]
                       AND li.[SubCostCodeId] IS NOT NULL
                       AND li.[Hours] > 0
                       AND ISNULL(scc.[IsHoldingCode], 0) = 0
                 ) x)
             ELSE NULL END AS [FinalCodeIds]
    FROM dbo.[ContractLaborCodingSuggestion] s
    INNER JOIN dbo.[ContractLabor] cl ON cl.[Id] = s.[ContractLaborId]
    WHERE s.[Status] = N'done'
      AND (@Since IS NULL OR s.[WorkDate] >= @Since);
END
GO
