-- Expense coding queue read-model: QBO Purchase lines at GL account
-- "Cost of construction : NEED TO CATEGORIZE" (58999), still uncoded: the
-- qbo.PurchaseLine.IsUncodedPlaceholder column (label AND no ItemRef, U-652d3). Do NOT loosen to
-- NULL / NEED TO UPDATE (that wrongly sweeps item-based lines).
--
-- Access (U-652d8): every scan uses the assert_accessible rule — admin; else CodingSuggestion.ExpenseId
-- via UserCanAccessExpense; else its project; else admin only. A line with NO suggestion row yet (cs.Id
-- NULL; nothing to write to) is scoped by its purchase's dbo.Expense, by QBO identity. A seeded row never
-- uses that fallback, so read and write agree exactly (the heal sweeper stamps ExpenseId within 15 min).
--
-- Seeding (U-652d10): ReadUnseededCodingPurchases (below) lists the purchases the QBO pull still has to
-- seed; the queue read itself no longer writes.
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
        WHERE pl.[IsUncodedPlaceholder] = 1
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
    LEFT JOIN [dbo].[Expense] e  -- at most one: UQ_Expense_QboId_RealmId
        ON e.[QboId] = r.[QboPurchaseQboId]
       AND e.[RealmId] = r.[RealmId]
    WHERE r.[Rn] = 1
      AND (
          -- U-652d8: the same scope as CodingSuggestionService.assert_accessible (U-652d7): admin; else the
          -- suggestion's Expense (an UNSEEDED line: the purchase's, by QBO identity); else its project; else admin only.
          @ActorIsSystemAdmin = 1
          OR (COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END) IS NOT NULL
              AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END)) = 1)
          OR (COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END) IS NULL
              AND COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NOT NULL
              AND dbo.UserCanAccessProject(
                      @ActorUserId, @ActorIsSystemAdmin,
                      COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1)
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
            LEFT JOIN [dbo].[Expense] e
                ON e.[QboId] = p.[QboId]
               AND e.[RealmId] = p.[RealmId]
            WHERE pl.[IsUncodedPlaceholder] = 1
              AND (@RealmId IS NULL OR p.[RealmId] = @RealmId)
              AND (
                  -- U-652d8: the same scope as CodingSuggestionService.assert_accessible (U-652d7): admin; else the
                  -- suggestion's Expense (an UNSEEDED line: the purchase's, by QBO identity); else its project; else admin only.
                  @ActorIsSystemAdmin = 1
                  OR (COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END) IS NOT NULL
                      AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END)) = 1)
                  OR (COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END) IS NULL
                      AND COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NOT NULL
                      AND dbo.UserCanAccessProject(
                              @ActorUserId, @ActorIsSystemAdmin,
                              COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1)
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
    LEFT JOIN [dbo].[Expense] e
        ON e.[QboId] = cs.[QboPurchaseQboId]
       AND e.[RealmId] = cs.[RealmId]
    WHERE cs.[TargetKind] = N'expense'
      AND (@RealmId IS NULL OR cs.[RealmId] = @RealmId)
      AND (@SinceCutoff IS NULL OR cs.[CreatedDatetime] >= @SinceCutoff)
      AND (
          -- U-652d8: the same scope as CodingSuggestionService.assert_accessible (U-652d7): admin; else the
          -- suggestion's Expense (an UNSEEDED line: the purchase's, by QBO identity); else its project; else admin only.
          @ActorIsSystemAdmin = 1
          OR (COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END) IS NOT NULL
              AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END)) = 1)
          OR (COALESCE(cs.[ExpenseId], CASE WHEN cs.[Id] IS NULL THEN e.[Id] END) IS NULL
              AND COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NOT NULL
              AND dbo.UserCanAccessProject(
                      @ActorUserId, @ActorIsSystemAdmin,
                      COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1)
      );
END;
GO

-- U-652d10: purchases the pull must seed — at least one uncoded line (IsUncodedPlaceholder) and no
-- CodingSuggestion row yet (MERGE key: RealmId + QboPurchaseQboId). Read-only; the caller upserts each one.
-- Oldest staging row first, capped per call so one tick never runs long; the rest wait for the next tick.
CREATE OR ALTER PROCEDURE ReadUnseededCodingPurchases
(
    @RealmId NVARCHAR(50) = NULL,
    @Limit INT = 200
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @Limit IS NULL OR @Limit < 1 SET @Limit = 200;

    SELECT TOP (@Limit)
        p.[Id] AS [QboPurchaseId],
        p.[QboId] AS [QboPurchaseQboId],
        p.[RealmId],
        p.[EntityRefValue] AS [VendorQboId]
    FROM [qbo].[Purchase] p
    WHERE (@RealmId IS NULL OR p.[RealmId] = @RealmId)
      AND EXISTS (
          SELECT 1 FROM [qbo].[PurchaseLine] pl
          WHERE pl.[QboPurchaseId] = p.[Id] AND pl.[IsUncodedPlaceholder] = 1
      )
      AND NOT EXISTS (
          SELECT 1 FROM [dbo].[CodingSuggestion] cs
          WHERE cs.[RealmId] = p.[RealmId] AND cs.[QboPurchaseQboId] = p.[QboId]
      )
    ORDER BY p.[Id];
END;
GO
