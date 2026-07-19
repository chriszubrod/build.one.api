-- Expense coding queue read-model: QBO Purchase lines at GL account
-- "Cost of construction : NEED TO CATEGORIZE" (58999). Strict match only —
-- do NOT loosen to NULL / NEED TO UPDATE (that wrongly sweeps item-based lines).
--
-- Run: python scripts/run_sql.py integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql

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
        pl.[Description] AS [LineDescription],
        eci.[PublicId] AS [CodingItemPublicId],
        eci.[Status] AS [CodingStatus],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[FlagReason],
        eci.[VendorId] AS [CodingVendorId],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt]
    FROM [qbo].[PurchaseLine] pl
    INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
    LEFT JOIN [dbo].[ExpenseCodingItem] eci ON eci.[QboPurchaseLineId] = pl.[Id]
    WHERE pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
      AND (@RealmId IS NULL OR p.[RealmId] = @RealmId)
      -- U-059: UserProject row-scoping (mirrors the Expense ledger). A coding
      -- row has no committed Expense; its project is the coding item's resolved
      -- project (Confirmed, else Suggested). Uncoded rows (no project yet) stay
      -- visible to every EXPENSES user — the queue's whole purpose is triaging
      -- them. Admins bypass. Non-admins see a coded row only via UserProject.
      AND (
          @ActorIsSystemAdmin = 1
          OR COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId]) IS NULL
          OR dbo.UserCanAccessProject(
                 @ActorUserId, @ActorIsSystemAdmin,
                 COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId])) = 1
      )
    ORDER BY TRY_CONVERT(DATE, p.[TxnDate], 23) DESC, p.[DocNumber], pl.[LineNum];
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

    -- TotalTargetLines hits the qbo.* staging tables (the live 58999 population,
    -- never @SinceDays-filtered); the per-status counts are a single scan of the
    -- instrumentation table via conditional aggregation. COUNT(CASE ... THEN 1 END)
    -- returns 0 (not NULL) over an empty set, matching the old COUNT(*) semantics.
    SELECT
        (
            SELECT COUNT(*)
            FROM [qbo].[PurchaseLine] pl
            INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
            LEFT JOIN [dbo].[ExpenseCodingItem] eci ON eci.[QboPurchaseLineId] = pl.[Id]
            WHERE pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
              AND (@RealmId IS NULL OR p.[RealmId] = @RealmId)
              AND (
                  @ActorIsSystemAdmin = 1
                  OR COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId]) IS NULL
                  OR dbo.UserCanAccessProject(
                         @ActorUserId, @ActorIsSystemAdmin,
                         COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId])) = 1
              )
        ) AS [TotalTargetLines],
        COUNT(CASE WHEN eci.[Status] = N'pending' THEN 1 END) AS [PendingCount],
        COUNT(CASE WHEN eci.[Status] = N'suggested' THEN 1 END) AS [SuggestedCount],
        COUNT(CASE WHEN eci.[Status] = N'flagged' THEN 1 END) AS [FlaggedCount],
        COUNT(CASE WHEN eci.[Status] = N'confirmed' THEN 1 END) AS [ConfirmedCount],
        COUNT(CASE WHEN eci.[Status] = N'enqueued' THEN 1 END) AS [EnqueuedCount],
        COUNT(CASE WHEN eci.[Status] = N'written' THEN 1 END) AS [WrittenCount],
        COUNT(CASE WHEN eci.[Status] = N'changed_in_qbo' THEN 1 END) AS [ChangedInQboCount],
        COUNT(CASE WHEN eci.[Status] = N'error' THEN 1 END) AS [ErrorCount],
        COUNT(CASE WHEN eci.[Status] = N'written' AND eci.[WasOverridden] = 0 THEN 1 END) AS [AcceptedCount],
        COUNT(CASE WHEN eci.[Status] = N'written' AND eci.[WasOverridden] = 1 THEN 1 END) AS [OverriddenCount]
    FROM [dbo].[ExpenseCodingItem] eci
    WHERE (@RealmId IS NULL OR eci.[RealmId] = @RealmId)
      AND (@SinceCutoff IS NULL OR eci.[CreatedDatetime] >= @SinceCutoff)
      AND (
          @ActorIsSystemAdmin = 1
          OR COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId]) IS NULL
          OR dbo.UserCanAccessProject(
                 @ActorUserId, @ActorIsSystemAdmin,
                 COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId])) = 1
      );
END;
GO
