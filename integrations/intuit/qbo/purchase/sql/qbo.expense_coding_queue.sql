-- Expense coding queue read-model: QBO Purchase lines at GL account
-- "Cost of construction : NEED TO CATEGORIZE" (58999). Strict match only —
-- do NOT loosen to NULL / NEED TO UPDATE (that wrongly sweeps item-based lines).
--
-- Run: ./.venv/bin/python scripts/run_sql.py integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql

GO

CREATE OR ALTER PROCEDURE ReadExpenseCodingQueue
(
    @RealmId NVARCHAR(50) = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH Uncategorized AS (
        SELECT
            p.[Id] AS [QboPurchaseId],
            p.[PublicId] AS [QboPurchasePublicId],
            p.[QboId] AS [QboPurchaseQboId],
            p.[SyncToken],
            p.[RealmId],
            p.[EntityRefValue] AS [VendorQboId],
            p.[EntityRefName] AS [VendorName],
            p.[Credit],
            p.[TotalAmt],
            p.[TxnDate],
            p.[DocNumber],
            p.[PrivateNote],
            pl.[Id] AS [QboPurchaseLineId],
            pl.[QboLineId],
            pl.[LineNum],
            pl.[Amount] AS [LineAmount],
            pl.[Description] AS [LineDescription]
        FROM [qbo].[PurchaseLine] pl
        INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
        WHERE pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
          AND (@RealmId IS NULL OR p.[RealmId] = @RealmId)
    ),
    Ranked AS (
        -- One pass over Uncategorized: the per-purchase count is a window aggregate.
        SELECT
            u.*,
            COUNT(*) OVER (PARTITION BY u.[QboPurchaseId]) AS [UncategorizedLineCount],
            ROW_NUMBER() OVER (PARTITION BY u.[QboPurchaseId] ORDER BY u.[QboPurchaseLineId]) AS [Rn]
        FROM Uncategorized u
    )
    SELECT
        r.[QboPurchaseId],
        r.[QboPurchasePublicId],
        r.[QboPurchaseQboId],
        r.[SyncToken],
        r.[RealmId],
        r.[VendorQboId],
        r.[VendorName],
        r.[Credit],
        r.[TotalAmt],
        r.[TxnDate],
        r.[DocNumber],
        r.[PrivateNote],
        r.[QboPurchaseLineId],
        r.[QboLineId],
        r.[LineNum],
        r.[LineAmount],
        r.[LineDescription],
        r.[UncategorizedLineCount],
        cs.[PublicId] AS [CodingItemPublicId],
        cs.[Status] AS [CodingStatus],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[FlagReason],
        cs.[VendorId] AS [CodingVendorId],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt]
    FROM Ranked r
    LEFT JOIN [dbo].[CodingSuggestion] cs
        ON cs.[TargetKind] = N'expense'
       AND cs.[RealmId] = r.[RealmId]
       AND cs.[QboPurchaseQboId] = r.[QboPurchaseQboId]
    WHERE r.[Rn] = 1
      AND (
          @ActorIsSystemAdmin = 1
          OR COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NULL
          OR dbo.UserCanAccessProject(
                 @ActorUserId, @ActorIsSystemAdmin,
                 COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1
      )
      AND (cs.[Status] IS NULL OR cs.[Status] NOT IN (N'written', N'resolved_externally'))
    ORDER BY TRY_CONVERT(DATE, r.[TxnDate], 23) DESC, r.[DocNumber], r.[LineNum];
END;
GO


CREATE OR ALTER PROCEDURE ReadExpenseCodingMetrics
(
    @RealmId NVARCHAR(50) = NULL,
    @SinceDays INT = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @SinceCutoff DATETIME2(3) = CASE
        WHEN @SinceDays IS NULL THEN NULL
        ELSE DATEADD(DAY, -@SinceDays, SYSUTCDATETIME())
    END;

    SELECT
        (
            SELECT COUNT(*)
            FROM [qbo].[PurchaseLine] pl
            INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
            LEFT JOIN [dbo].[CodingSuggestion] cs
                ON cs.[TargetKind] = N'expense'
               AND cs.[RealmId] = p.[RealmId]
               AND cs.[QboPurchaseQboId] = p.[QboId]
            WHERE pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
              AND (@RealmId IS NULL OR p.[RealmId] = @RealmId)
              AND (
                  @ActorIsSystemAdmin = 1
                  OR COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NULL
                  OR dbo.UserCanAccessProject(
                         @ActorUserId, @ActorIsSystemAdmin,
                         COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1
              )
        ) AS [TotalTargetLines],
        COUNT(CASE WHEN cs.[Status] = N'pending' THEN 1 END) AS [PendingCount],
        COUNT(CASE WHEN cs.[Status] = N'suggested' THEN 1 END) AS [SuggestedCount],
        COUNT(CASE WHEN cs.[Status] = N'flagged' THEN 1 END) AS [FlaggedCount],
        COUNT(CASE WHEN cs.[Status] = N'confirmed' THEN 1 END) AS [ConfirmedCount],
        COUNT(CASE WHEN cs.[Status] = N'enqueued' THEN 1 END) AS [EnqueuedCount],
        COUNT(CASE WHEN cs.[Status] = N'written' THEN 1 END) AS [WrittenCount],
        COUNT(CASE WHEN cs.[Status] = N'changed_in_qbo' THEN 1 END) AS [ChangedInQboCount],
        COUNT(CASE WHEN cs.[Status] = N'resolved_externally' THEN 1 END) AS [ResolvedExternallyCount],
        COUNT(CASE WHEN cs.[Status] = N'error' THEN 1 END) AS [ErrorCount],
        COUNT(CASE WHEN cs.[Status] = N'written' AND cs.[WasOverridden] = 0 THEN 1 END) AS [AcceptedCount],
        COUNT(CASE WHEN cs.[Status] = N'written' AND cs.[WasOverridden] = 1 THEN 1 END) AS [OverriddenCount]
    FROM [dbo].[CodingSuggestion] cs
    WHERE cs.[TargetKind] = N'expense'
      AND (@RealmId IS NULL OR cs.[RealmId] = @RealmId)
      AND (@SinceCutoff IS NULL OR cs.[CreatedDatetime] >= @SinceCutoff)
      AND (
          @ActorIsSystemAdmin = 1
          OR COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NULL
          OR dbo.UserCanAccessProject(
                 @ActorUserId, @ActorIsSystemAdmin,
                 COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1
      );
END;
GO
