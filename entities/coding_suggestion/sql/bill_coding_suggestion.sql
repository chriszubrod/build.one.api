-- Bill coding suggestion read-model + upsert (U-653a BITE 1).
-- Standalone file — history aggregation and bill-target writes live here, not in dbo.coding_suggestion.sql.
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/coding_suggestion/sql/bill_coding_suggestion.sql

GO

-- Upgrade an earlier definition that lacked IsDraft in INCLUDE (the history procs filter
-- on IsDraft): drop it so the guarded CREATE below installs the covering form.
IF EXISTS (
       SELECT 1 FROM sys.indexes i
       WHERE i.name = 'IX_Bill_VendorId_CreatedDatetime' AND i.object_id = OBJECT_ID('dbo.Bill')
   )
   AND NOT EXISTS (
       SELECT 1
       FROM sys.indexes i
       JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
       JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
       WHERE i.name = 'IX_Bill_VendorId_CreatedDatetime' AND i.object_id = OBJECT_ID('dbo.Bill')
         AND c.name = 'IsDraft' AND ic.is_included_column = 1
   )
BEGIN
    DROP INDEX [IX_Bill_VendorId_CreatedDatetime] ON dbo.[Bill];
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = 'IX_Bill_VendorId_CreatedDatetime'
         AND object_id = OBJECT_ID('dbo.Bill')
   )
BEGIN
    CREATE NONCLUSTERED INDEX [IX_Bill_VendorId_CreatedDatetime]
        ON dbo.[Bill] ([VendorId], [CreatedDatetime])
        INCLUDE ([IsDraft]);
END
GO

-- Shared bill-level dominant SubCostCode vote (eligible bills → line agg → tie-break → project from MinLineId).
CREATE OR ALTER FUNCTION dbo.BillDominantVoteForVendor
(
    @VendorId BIGINT,
    @AsOfDatetime DATETIME2(3),
    @ExcludeBillId BIGINT
)
RETURNS TABLE
AS
RETURN
(
    WITH EligibleBills AS (
        SELECT b.[Id] AS [BillId]
        FROM dbo.[Bill] b
        WHERE b.[VendorId] = @VendorId
          AND b.[IsDraft] = 0
          AND (@AsOfDatetime IS NULL OR b.[CreatedDatetime] < @AsOfDatetime)
          AND (@ExcludeBillId IS NULL OR b.[Id] <> @ExcludeBillId)
    ),
    LineAgg AS (
        SELECT
            bli.[BillId],
            bli.[SubCostCodeId],
            SUM(ABS(bli.[Amount])) AS [AbsTotal],
            MIN(bli.[Id]) AS [MinLineId]
        FROM dbo.[BillLineItem] bli
        INNER JOIN EligibleBills eb ON eb.[BillId] = bli.[BillId]
        WHERE bli.[SubCostCodeId] IS NOT NULL
        GROUP BY bli.[BillId], bli.[SubCostCodeId]
    ),
    DominantScc AS (
        SELECT
            la.[BillId],
            la.[SubCostCodeId],
            la.[MinLineId],
            ROW_NUMBER() OVER (
                PARTITION BY la.[BillId]
                ORDER BY la.[AbsTotal] DESC, la.[MinLineId] ASC
            ) AS [Rn]
        FROM LineAgg la
    )
    SELECT
        ds.[BillId],
        ds.[SubCostCodeId],
        bli.[ProjectId] AS [DominantProjectId]
    FROM DominantScc ds
    INNER JOIN dbo.[BillLineItem] bli ON bli.[Id] = ds.[MinLineId]
    WHERE ds.[Rn] = 1
);
GO

-- Contract: Bill-grain vendor history for SubCostCode prediction (U-639 §5.5, U-653a).
-- Params: @VendorId required; @ProjectId filters to bills whose dominant project matches;
-- @AsOfDatetime excludes bills created at/after the clock (strict <); @ExcludeBillId omits the scored bill.
-- Returns one row (TopSubCostCodeId, TopCount, SecondCount, TotalCount, Number, Name) when TotalCount >= 3, else empty.
CREATE OR ALTER PROCEDURE ReadVendorDominantSubCostCodeForBills
(
    @VendorId BIGINT,
    @ProjectId BIGINT = NULL,
    @AsOfDatetime DATETIME2(3) = NULL,
    @ExcludeBillId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH BillVote AS (
        SELECT
            bv.[BillId],
            bv.[SubCostCodeId],
            bv.[DominantProjectId]
        FROM dbo.BillDominantVoteForVendor(@VendorId, @AsOfDatetime, @ExcludeBillId) bv
    ),
    FilteredVotes AS (
        SELECT bv.[SubCostCodeId]
        FROM BillVote bv
        WHERE @ProjectId IS NULL OR bv.[DominantProjectId] = @ProjectId
    ),
    VoteCounts AS (
        SELECT
            fv.[SubCostCodeId],
            COUNT(*) AS [VoteCount]
        FROM FilteredVotes fv
        GROUP BY fv.[SubCostCodeId]
    ),
    Totals AS (
        SELECT COUNT(*) AS [TotalCount]
        FROM FilteredVotes
    ),
    Ranked AS (
        SELECT
            vc.[SubCostCodeId],
            vc.[VoteCount],
            ROW_NUMBER() OVER (
                ORDER BY vc.[VoteCount] DESC, vc.[SubCostCodeId] ASC
            ) AS [RankN]
        FROM VoteCounts vc
    )
    SELECT
        r1.[SubCostCodeId] AS [TopSubCostCodeId],
        r1.[VoteCount] AS [TopCount],
        COALESCE(r2.[VoteCount], 0) AS [SecondCount],
        t.[TotalCount],
        scc.[PublicId] AS [SubCostCodePublicId],
        scc.[Number],
        scc.[Name]
    FROM Totals t
    INNER JOIN Ranked r1 ON r1.[RankN] = 1
    LEFT JOIN Ranked r2 ON r2.[RankN] = 2
    INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = r1.[SubCostCodeId]
    WHERE t.[TotalCount] >= 3;
END;
GO

-- Contract: Bill-grain vendor history for Project prediction (Mode B, U-653a). Same bill population and dominant-line rule as ReadVendorDominantSubCostCodeForBills; votes by dominant ProjectId.
CREATE OR ALTER PROCEDURE ReadVendorDominantProjectForBills
(
    @VendorId BIGINT,
    @AsOfDatetime DATETIME2(3) = NULL,
    @ExcludeBillId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH BillVote AS (
        SELECT
            bv.[BillId],
            bv.[DominantProjectId]
        FROM dbo.BillDominantVoteForVendor(@VendorId, @AsOfDatetime, @ExcludeBillId) bv
    ),
    FilteredVotes AS (
        SELECT bv.[DominantProjectId]
        FROM BillVote bv
        WHERE bv.[DominantProjectId] IS NOT NULL
    ),
    VoteCounts AS (
        SELECT
            fv.[DominantProjectId],
            COUNT(*) AS [VoteCount]
        FROM FilteredVotes fv
        GROUP BY fv.[DominantProjectId]
    ),
    Totals AS (
        SELECT COUNT(*) AS [TotalCount]
        FROM FilteredVotes
    ),
    Ranked AS (
        SELECT
            vc.[DominantProjectId],
            vc.[VoteCount],
            ROW_NUMBER() OVER (
                ORDER BY vc.[VoteCount] DESC, vc.[DominantProjectId] ASC
            ) AS [RankN]
        FROM VoteCounts vc
    )
    SELECT
        r1.[DominantProjectId] AS [TopProjectId],
        r1.[VoteCount] AS [TopCount],
        COALESCE(r2.[VoteCount], 0) AS [SecondCount],
        t.[TotalCount]
    FROM Totals t
    INNER JOIN Ranked r1 ON r1.[RankN] = 1
    LEFT JOIN Ranked r2 ON r2.[RankN] = 2
    WHERE t.[TotalCount] >= 3;
END;
GO

-- Contract: MERGE one CodingSuggestion row per Bill (TargetKind bill); updates suggestion fields in place; never mutates confirmation or QBO columns.
CREATE OR ALTER PROCEDURE UpsertCodingSuggestionForBill
(
    @BillId BIGINT,
    @VendorId BIGINT = NULL,
    @SuggestedProjectId BIGINT = NULL,
    @SuggestedSubCostCodeId BIGINT = NULL,
    @SuggestionSource NVARCHAR(50) = NULL,
    @SuggestionReason NVARCHAR(1024) = NULL,
    @SuggestionConfidence DECIMAL(5,4) = NULL,
    @FeaturesJson NVARCHAR(MAX) = NULL,
    @ModelVersion NVARCHAR(50) = NULL,
    @PrefillState NVARCHAR(12) = NULL,
    @CreatedByUserId BIGINT = NULL,
    @ReplaceSuggestion BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpsertedId BIGINT;

    MERGE dbo.[CodingSuggestion] WITH (HOLDLOCK) AS target
    USING (
        SELECT @BillId AS [BillId]
    ) AS source
    ON target.[BillId] = source.[BillId]
       AND target.[TargetKind] = N'bill'
    WHEN MATCHED THEN
        UPDATE SET
            [VendorId] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @VendorId
                WHEN @VendorId IS NOT NULL THEN @VendorId
                ELSE target.[VendorId]
            END,
            [SuggestedProjectId] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @SuggestedProjectId
                WHEN @SuggestedProjectId IS NOT NULL THEN @SuggestedProjectId
                ELSE target.[SuggestedProjectId]
            END,
            [SuggestedSubCostCodeId] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @SuggestedSubCostCodeId
                WHEN @SuggestedSubCostCodeId IS NOT NULL THEN @SuggestedSubCostCodeId
                ELSE target.[SuggestedSubCostCodeId]
            END,
            [SuggestionSource] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @SuggestionSource
                WHEN @SuggestionSource IS NOT NULL THEN @SuggestionSource
                ELSE target.[SuggestionSource]
            END,
            [SuggestionReason] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @SuggestionReason
                WHEN @SuggestionReason IS NOT NULL THEN @SuggestionReason
                ELSE target.[SuggestionReason]
            END,
            [SuggestionConfidence] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @SuggestionConfidence
                WHEN @SuggestionConfidence IS NOT NULL THEN @SuggestionConfidence
                ELSE target.[SuggestionConfidence]
            END,
            [FeaturesJson] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @FeaturesJson
                WHEN @FeaturesJson IS NOT NULL THEN @FeaturesJson
                ELSE target.[FeaturesJson]
            END,
            [ModelVersion] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @ModelVersion
                WHEN @ModelVersion IS NOT NULL THEN @ModelVersion
                ELSE target.[ModelVersion]
            END,
            [PrefillState] = CASE
                WHEN @ReplaceSuggestion = 1 THEN @PrefillState
                WHEN @PrefillState IS NOT NULL THEN @PrefillState
                ELSE target.[PrefillState]
            END,
            [SuggestedAt] = @Now,
            [ModifiedDatetime] = @Now
    WHEN NOT MATCHED THEN
        INSERT (
            [TargetKind],
            [BillId],
            [VendorId],
            [SuggestedProjectId],
            [SuggestedSubCostCodeId],
            [SuggestionSource],
            [SuggestionReason],
            [SuggestionConfidence],
            [FeaturesJson],
            [ModelVersion],
            [PrefillState],
            [SuggestedAt],
            [Status],
            [CreatedByUserId],
            [CreatedDatetime],
            [ModifiedDatetime]
        )
        VALUES (
            N'bill',
            @BillId,
            @VendorId,
            @SuggestedProjectId,
            @SuggestedSubCostCodeId,
            @SuggestionSource,
            @SuggestionReason,
            @SuggestionConfidence,
            @FeaturesJson,
            @ModelVersion,
            @PrefillState,
            @Now,
            N'pending',
            COALESCE(@CreatedByUserId, 17),
            @Now,
            @Now
        );

    SELECT @UpsertedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[BillId] = @BillId
      AND cs.[TargetKind] = N'bill';

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpsertedId;

    COMMIT TRANSACTION;
END;
GO

-- Contract: Read the bill-target CodingSuggestion row by BillId (at most one per UQ_CodingSuggestion_BillId).
CREATE OR ALTER PROCEDURE ReadCodingSuggestionByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[BillId] = @BillId
      AND cs.[TargetKind] = N'bill';
END;
GO
