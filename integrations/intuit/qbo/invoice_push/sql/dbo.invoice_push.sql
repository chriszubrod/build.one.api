-- U-778d1 b2: READ-ONLY fact sprocs for the QBO invoice-push dry run (design u778d sections 3-4).
-- Contract: every procedure here only SELECTs. The line-fact procs take @IdsJson (a JSON array of BIGINT source-line
-- ids, read with OPENJSON, DISTINCT: a repeated id never repeats a row) and @RealmId, and return ONE row per FOUND line
-- (a missing id has no row). A dbo QboId is returned only when its row is stamped in @RealmId. Staged (qbo.*) lookups
-- are realm-scoped, so the same QboId in another realm never answers. StagedChargeIds = the DISTINCT staged
-- ReimburseChargeQboIds of EVERY staged line matching the line's identity (no TOP 1: duplicates collapse, a
-- disagreement shows). InvoiceLineItemInvoiceQboIds is aligned with InvoiceLineItemIds (same ORDER BY ili.Id): each
-- referencing ILI's dbo.Invoice QboId stamped in @RealmId, else '-'.
-- Staged INVOICE evidence is NOT read here: it is keyed by the LIVE charge id, via
-- ReadInvoicePushStagedInvoiceLinksByChargeIds(@ChargeIdsJson, @RealmId) -> (ChargeId, QboInvoiceQboId,
-- QboInvoiceLineId) for every realm-scoped staged qbo.InvoiceLine with LinkedTxnType 'ReimburseCharge'.
-- Sprocs: ReadInvoicePushBillLineFacts, ReadInvoicePushExpenseLineFacts, ReadInvoicePushBillCreditLineFacts,
-- ReadInvoicePushProjectFacts, ReadInvoicePushStagedInvoiceLinksByChargeIds.


CREATE OR ALTER PROCEDURE ReadInvoicePushBillLineFacts
(
    @IdsJson NVARCHAR(MAX),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        l.[Id]                AS [LineId],
        l.[ProjectId]         AS [ProjectId],
        l.[SubCostCodeId]     AS [SubCostCodeId],
        CAST(COALESCE(scc.[IsHoldingCode], 0) AS BIT) AS [IsHoldingCode],
        l.[Amount]            AS [Amount],
        l.[Markup]            AS [Markup],
        l.[Price]             AS [Price],
        CAST(COALESCE(l.[IsBillable], 0) AS BIT) AS [IsBillable],
        CAST(COALESCE(l.[IsBilled], 0) AS BIT)   AS [IsBilled],
        CASE WHEN l.[RealmId] = @RealmId THEN l.[QboId] END AS [LineQboId],
        h.[Id]                AS [ParentId],
        CASE WHEN h.[RealmId] = @RealmId THEN h.[QboId] END AS [ParentQboId],
        CAST(h.[IsDraft] AS BIT) AS [ParentIsDraft],
        sl.[StagedChargeIds] AS [StagedChargeIds],
        (SELECT STRING_AGG(CAST(ili.[Id] AS NVARCHAR(MAX)), N',') WITHIN GROUP (ORDER BY ili.[Id])
           FROM [dbo].[InvoiceLineItem] ili
          WHERE ili.[BillLineItemId] = l.[Id]) AS [InvoiceLineItemIds],
        (SELECT STRING_AGG(COALESCE(CASE WHEN di.[RealmId] = @RealmId THEN di.[QboId] END, N'-'), N',')
                WITHIN GROUP (ORDER BY ili.[Id])
           FROM [dbo].[InvoiceLineItem] ili
           LEFT JOIN [dbo].[Invoice] di ON di.[Id] = ili.[InvoiceId]
          WHERE ili.[BillLineItemId] = l.[Id]) AS [InvoiceLineItemInvoiceQboIds]
    FROM (SELECT DISTINCT [Id] FROM OPENJSON(@IdsJson) WITH ([Id] BIGINT '$')) ids
    JOIN [dbo].[BillLineItem] l ON l.[Id] = ids.[Id]
    JOIN [dbo].[Bill] h ON h.[Id] = l.[BillId]
    LEFT JOIN [dbo].[SubCostCode] scc ON scc.[Id] = l.[SubCostCodeId]
    OUTER APPLY
    (
        SELECT STRING_AGG(x.[ReimburseChargeQboId], N',') WITHIN GROUP (ORDER BY x.[ReimburseChargeQboId]) AS [StagedChargeIds]
          FROM (SELECT DISTINCT s.[ReimburseChargeQboId]
                  FROM [qbo].[Bill] sh
                  JOIN [qbo].[BillLine] s ON s.[QboBillId] = sh.[Id]
                 WHERE sh.[QboId] = CASE WHEN h.[RealmId] = @RealmId THEN h.[QboId] END
                   AND sh.[RealmId] = @RealmId
                   AND s.[QboLineId] = CASE WHEN l.[RealmId] = @RealmId THEN l.[QboId] END
                   AND s.[ReimburseChargeQboId] IS NOT NULL) x
    ) sl
    ORDER BY l.[Id];
END
GO

CREATE OR ALTER PROCEDURE ReadInvoicePushExpenseLineFacts
(
    @IdsJson NVARCHAR(MAX),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        l.[Id]                AS [LineId],
        l.[ProjectId]         AS [ProjectId],
        l.[SubCostCodeId]     AS [SubCostCodeId],
        CAST(COALESCE(scc.[IsHoldingCode], 0) AS BIT) AS [IsHoldingCode],
        l.[Amount]            AS [Amount],
        l.[Markup]            AS [Markup],
        l.[Price]             AS [Price],
        CAST(COALESCE(l.[IsBillable], 0) AS BIT) AS [IsBillable],
        CAST(COALESCE(l.[IsBilled], 0) AS BIT)   AS [IsBilled],
        CASE WHEN l.[RealmId] = @RealmId THEN l.[QboId] END AS [LineQboId],
        h.[Id]                AS [ParentId],
        CASE WHEN h.[RealmId] = @RealmId THEN h.[QboId] END AS [ParentQboId],
        CAST(h.[IsDraft] AS BIT) AS [ParentIsDraft],
        sl.[StagedChargeIds] AS [StagedChargeIds],
        (SELECT STRING_AGG(CAST(ili.[Id] AS NVARCHAR(MAX)), N',') WITHIN GROUP (ORDER BY ili.[Id])
           FROM [dbo].[InvoiceLineItem] ili
          WHERE ili.[ExpenseLineItemId] = l.[Id]) AS [InvoiceLineItemIds],
        (SELECT STRING_AGG(COALESCE(CASE WHEN di.[RealmId] = @RealmId THEN di.[QboId] END, N'-'), N',')
                WITHIN GROUP (ORDER BY ili.[Id])
           FROM [dbo].[InvoiceLineItem] ili
           LEFT JOIN [dbo].[Invoice] di ON di.[Id] = ili.[InvoiceId]
          WHERE ili.[ExpenseLineItemId] = l.[Id]) AS [InvoiceLineItemInvoiceQboIds]
    FROM (SELECT DISTINCT [Id] FROM OPENJSON(@IdsJson) WITH ([Id] BIGINT '$')) ids
    JOIN [dbo].[ExpenseLineItem] l ON l.[Id] = ids.[Id]
    JOIN [dbo].[Expense] h ON h.[Id] = l.[ExpenseId]
    LEFT JOIN [dbo].[SubCostCode] scc ON scc.[Id] = l.[SubCostCodeId]
    OUTER APPLY
    (
        SELECT STRING_AGG(x.[ReimburseChargeQboId], N',') WITHIN GROUP (ORDER BY x.[ReimburseChargeQboId]) AS [StagedChargeIds]
          FROM (SELECT DISTINCT s.[ReimburseChargeQboId]
                  FROM [qbo].[Purchase] sh
                  JOIN [qbo].[PurchaseLine] s ON s.[QboPurchaseId] = sh.[Id]
                 WHERE sh.[QboId] = CASE WHEN h.[RealmId] = @RealmId THEN h.[QboId] END
                   AND sh.[RealmId] = @RealmId
                   AND s.[QboLineId] = CASE WHEN l.[RealmId] = @RealmId THEN l.[QboId] END
                   AND s.[ReimburseChargeQboId] IS NOT NULL) x
    ) sl
    ORDER BY l.[Id];
END
GO

CREATE OR ALTER PROCEDURE ReadInvoicePushBillCreditLineFacts
(
    @IdsJson NVARCHAR(MAX),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        l.[Id]                AS [LineId],
        l.[ProjectId]         AS [ProjectId],
        l.[SubCostCodeId]     AS [SubCostCodeId],
        CAST(COALESCE(scc.[IsHoldingCode], 0) AS BIT) AS [IsHoldingCode],
        l.[BillableAmount]    AS [BillableAmount],
        CAST(COALESCE(l.[IsBillable], 0) AS BIT) AS [IsBillable],
        CAST(COALESCE(l.[IsBilled], 0) AS BIT)   AS [IsBilled],
        CASE WHEN l.[RealmId] = @RealmId THEN l.[QboId] END AS [LineQboId],
        h.[Id]                AS [ParentId],
        CASE WHEN h.[RealmId] = @RealmId THEN h.[QboId] END AS [ParentQboId],
        CAST(h.[IsDraft] AS BIT) AS [ParentIsDraft],
        sl.[StagedChargeIds] AS [StagedChargeIds],
        (SELECT STRING_AGG(CAST(ili.[Id] AS NVARCHAR(MAX)), N',') WITHIN GROUP (ORDER BY ili.[Id])
           FROM [dbo].[InvoiceLineItem] ili
          WHERE ili.[BillCreditLineItemId] = l.[Id]) AS [InvoiceLineItemIds],
        (SELECT STRING_AGG(COALESCE(CASE WHEN di.[RealmId] = @RealmId THEN di.[QboId] END, N'-'), N',')
                WITHIN GROUP (ORDER BY ili.[Id])
           FROM [dbo].[InvoiceLineItem] ili
           LEFT JOIN [dbo].[Invoice] di ON di.[Id] = ili.[InvoiceId]
          WHERE ili.[BillCreditLineItemId] = l.[Id]) AS [InvoiceLineItemInvoiceQboIds]
    FROM (SELECT DISTINCT [Id] FROM OPENJSON(@IdsJson) WITH ([Id] BIGINT '$')) ids
    JOIN [dbo].[BillCreditLineItem] l ON l.[Id] = ids.[Id]
    JOIN [dbo].[BillCredit] h ON h.[Id] = l.[BillCreditId]
    LEFT JOIN [dbo].[SubCostCode] scc ON scc.[Id] = l.[SubCostCodeId]
    OUTER APPLY
    (
        SELECT STRING_AGG(x.[ReimburseChargeQboId], N',') WITHIN GROUP (ORDER BY x.[ReimburseChargeQboId]) AS [StagedChargeIds]
          FROM (SELECT DISTINCT s.[ReimburseChargeQboId]
                  FROM [qbo].[VendorCredit] sh
                  JOIN [qbo].[VendorCreditLine] s ON s.[QboVendorCreditId] = sh.[Id]
                 WHERE sh.[QboId] = CASE WHEN h.[RealmId] = @RealmId THEN h.[QboId] END
                   AND sh.[RealmId] = @RealmId
                   AND s.[QboLineId] = CASE WHEN l.[RealmId] = @RealmId THEN l.[QboId] END
                   AND s.[ReimburseChargeQboId] IS NOT NULL) x
    ) sl
    ORDER BY l.[Id];
END
GO

CREATE OR ALTER PROCEDURE ReadInvoicePushProjectFacts
(
    @ProjectId BIGINT,
    @DocNumber NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.[Id]      AS [ProjectId],
        p.[QboId]   AS [QboId],
        p.[RealmId] AS [RealmId],
        CAST(CASE WHEN EXISTS (SELECT 1 FROM [dbo].[Invoice] i
                                WHERE i.[ProjectId] = p.[Id] AND i.[InvoiceNumber] = @DocNumber)
                  THEN 1 ELSE 0 END AS BIT) AS [DocNumberUsedLocally],
        (SELECT STRING_AGG(COALESCE(t.[Q], N'-'), N',') WITHIN GROUP (ORDER BY t.[Id])
           FROM (SELECT i.[Id], CASE WHEN i.[RealmId] = p.[RealmId] THEN i.[QboId] END AS [Q]
                   FROM [dbo].[Invoice] i
                  WHERE i.[ProjectId] = p.[Id] AND i.[InvoiceNumber] = @DocNumber) t) AS [DocNumberLocalInvoiceQboIds]
    FROM [dbo].[Project] p
    WHERE p.[Id] = @ProjectId;
END
GO

CREATE OR ALTER PROCEDURE ReadInvoicePushStagedInvoiceLinksByChargeIds
(
    @ChargeIdsJson NVARCHAR(MAX),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        sil.[LinkedTxnId] AS [ChargeId],
        si.[QboId]        AS [QboInvoiceQboId],
        sil.[QboLineId]   AS [QboInvoiceLineId]
    FROM (SELECT DISTINCT [Id] FROM OPENJSON(@ChargeIdsJson) WITH ([Id] NVARCHAR(50) '$')) ids
    JOIN [qbo].[InvoiceLine] sil ON sil.[LinkedTxnId] = ids.[Id] AND sil.[LinkedTxnType] = N'ReimburseCharge'
    JOIN [qbo].[Invoice] si ON si.[Id] = sil.[QboInvoiceId] AND si.[RealmId] = @RealmId
    ORDER BY sil.[LinkedTxnId], si.[QboId], sil.[QboLineId], sil.[Id];
END
GO
