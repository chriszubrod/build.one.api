-- ─────────────────────────────────────────────────────────────────────────
-- Budget variance engine (Phase 2, 2026-06-12).
--
-- ReadBudgetVarianceByProjectId: one row per SubCostCode touched by the
-- budget, any cost actual, or any draw — budgeted vs actual cost vs drawn.
-- ReadBudgetListRollups: per-budget contract value + drawn for the list page.
--
-- POLICY (locked 2026-06-11, see umbrella memory project_budget_entity.md):
--   * Drafts COUNT toward actual cost (bill/expense/credit lines incl. IsDraft=1).
--   * ContractLabor + EmployeeLabor COUNT as cost.
--   * Drawn = COMPLETED invoices only (Invoice.IsDraft = 0), strictly.
--   * Cost compares pre-markup (budget Amount vs source Amount/backed-out
--     cost); drawn compares billable (budget Price vs invoice Price). The
--     asymmetry is deliberate — do NOT "simplify" both sides to Price or the
--     markup margin appears on both sides and CostVariance silently breaks.
--
-- LANDMINES encoded below (each was independently verified against code):
--   * ContractLaborLineItem.Rate is a DAILY rate — Hours*Rate is ~8x wrong.
--     Cost = Price / (1 + Markup), exactly what bill generation does
--     (entities/contract_labor/business/bill_service.py::_get_amount).
--   * EmployeeLaborLineItem.Price / TotalAmount are markup-INCLUSIVE.
--     Cost = Hours * Rate (hourly). W2 labor NEVER becomes a Bill.
--   * No-double-count predicate for CL is the LINE-level BillLineItemId
--     (ON DELETE SET NULL — self-heals when a generated bill is deleted).
--     Header ContractLabor.BillLineItemId is legacy/first-line-only.
--     Non-billable CL lines never roll into a BLI, so they count as cost
--     regardless of the FK (policy: non-billable labor is still real cost).
--   * InvoiceLineItem.SubCostCodeId is NULL on QBO-pulled lines — resolve
--     SCC through the SOURCE line via COALESCE across all FOUR source FKs.
--     Branch on FK presence, NOT SourceType (QBO re-pull clobbers the label
--     back to 'Manual' — see TODO.md known issue).
--   * ILI.Price is stored as a POSITIVE magnitude even for credit-sourced
--     lines (entities/invoice/api/router.py:27-53) — negate BillCredit-
--     sourced lines and Expense-credit-sourced lines, and take Manual
--     lines' sign from ILI.Amount, or credits INCREASE drawn.
--   * Lines with NULL SubCostCodeId aggregate into the Uncategorized row
--     (SccId NULL). Lines with NULL ProjectId are invisible to any
--     project's variance — documented blind spot, out of scope v1.
--   * UnpricedLaborHours: pending-review labor has NULL Rate/Price and
--     contributes $0 cost — the column keeps the variance honest.
--   * Aggregate each family in its own GROUP BY, then join the aggregates.
--     No per-row UDFs / correlated EXISTS over line tables (the 113-second
--     Gap-1 lesson).
--
-- Read-only — no transactions. SET NOCOUNT ON per house pyodbc discipline.
-- ─────────────────────────────────────────────────────────────────────────
GO

CREATE OR ALTER PROCEDURE ReadBudgetVarianceByProjectId
(
    @ProjectId          BIGINT,
    @BudgetId           BIGINT = NULL,
    @ActorUserId        BIGINT = NULL,
    @ActorIsSystemAdmin BIT    = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Fail closed: @CanAccess gates the final SELECT so denied actors get an
    -- EMPTY result set (preserves the result-set shape for pyodbc).
    DECLARE @CanAccess BIT =
        dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, @ProjectId);

    -- The service passes the resolved @BudgetId so an ARCHIVED budget's
    -- variance reports its own revisions, never a different live budget's
    -- (the re-resolution below is the no-arg fallback only). Filtered unique
    -- index guarantees <= 1 live budget per project.
    IF @BudgetId IS NULL
        SET @BudgetId =
            (SELECT TOP 1 [Id] FROM dbo.[Budget]
             WHERE [ProjectId] = @ProjectId AND [Status] <> 'archived');

    WITH BudgetAgg AS (
        -- Contract value per SCC = SUM over APPROVED revisions only
        -- (Rev 0 + approved change-order deltas; negatives allowed).
        -- NULL SCC keys map to sentinel -1 (the Uncategorized bucket): the
        -- final projection equi-joins aggregates on SccId, and NULL = NULL
        -- is never true — without the sentinel the Uncategorized row would
        -- silently zero out (caught live on project 18: $8.1M of QBO-pulled
        -- Manual draw lines vanished).
        SELECT ISNULL(bli.[SubCostCodeId], -1) AS [SccId],
               SUM(ISNULL(bli.[Amount], 0)) AS [BudgetAmount],
               SUM(ISNULL(bli.[Price], 0))  AS [BudgetPrice]
        FROM dbo.[BudgetLineItem] bli
        INNER JOIN dbo.[BudgetRevision] br ON br.[Id] = bli.[BudgetRevisionId]
        WHERE br.[BudgetId] = @BudgetId
          AND br.[Status] = 'approved'
        GROUP BY ISNULL(bli.[SubCostCodeId], -1)
    ),
    BillAgg AS (
        SELECT ISNULL([SubCostCodeId], -1) AS [SccId],
               SUM(ISNULL([Amount], 0)) AS [BillCost]
        FROM dbo.[BillLineItem]
        WHERE [ProjectId] = @ProjectId          -- drafts included per policy
        GROUP BY ISNULL([SubCostCodeId], -1)
    ),
    ExpenseAgg AS (
        -- ExpenseLineItem amounts are stored POSITIVE with the header flag;
        -- expense credits (refunds) reduce cost.
        SELECT ISNULL(eli.[SubCostCodeId], -1) AS [SccId],
               SUM(CASE WHEN e.[IsCredit] = 1 THEN -ISNULL(eli.[Amount], 0)
                        ELSE ISNULL(eli.[Amount], 0) END) AS [ExpenseCost]
        FROM dbo.[ExpenseLineItem] eli
        INNER JOIN dbo.[Expense] e ON e.[Id] = eli.[ExpenseId]
        WHERE eli.[ProjectId] = @ProjectId      -- drafts included per policy
        GROUP BY ISNULL(eli.[SubCostCodeId], -1)
    ),
    CreditAgg AS (
        -- BillCreditLineItem amounts stored positive — subtracted from cost
        -- in the final projection. (No Markup/Price columns on this table.)
        SELECT ISNULL([SubCostCodeId], -1) AS [SccId],
               SUM(ISNULL([Amount], 0)) AS [CreditCost]
        FROM dbo.[BillCreditLineItem]
        WHERE [ProjectId] = @ProjectId          -- drafts included per policy
        GROUP BY ISNULL([SubCostCodeId], -1)
    ),
    ContractLaborAgg AS (
        SELECT ISNULL([SubCostCodeId], -1) AS [SccId],
               -- NULLIF guards Markup = -1 (no CHECK constraint on the column):
               -- the bad row contributes NULL → skipped by SUM ($0) instead of
               -- error 8134 bricking the whole variance call.
               SUM(ISNULL([Price], 0) / NULLIF(1 + ISNULL([Markup], 0), 0)) AS [ContractLaborCost],
               SUM(CASE WHEN [Price] IS NULL THEN ISNULL([Hours], 0) ELSE 0 END) AS [UnpricedHours]
        FROM dbo.[ContractLaborLineItem]
        WHERE [ProjectId] = @ProjectId
          AND [IsOverhead] = 0
          AND ([BillLineItemId] IS NULL OR [IsBillable] = 0)
        GROUP BY ISNULL([SubCostCodeId], -1)
    ),
    EmployeeLaborAgg AS (
        SELECT ISNULL([SubCostCodeId], -1) AS [SccId],
               SUM(ISNULL([Hours], 0) * ISNULL([Rate], 0)) AS [EmployeeLaborCost],
               SUM(CASE WHEN [Rate] IS NULL THEN ISNULL([Hours], 0) ELSE 0 END) AS [UnpricedHours]
        FROM dbo.[EmployeeLaborLineItem]
        WHERE [ProjectId] = @ProjectId
          AND [IsOverhead] = 0                  -- ALL statuses incl. 'invoiced'
        GROUP BY ISNULL([SubCostCodeId], -1)
    ),
    DrawnAgg AS (
        SELECT ISNULL(COALESCE(ili.[SubCostCodeId], bli.[SubCostCodeId], eli.[SubCostCodeId],
                        bcli.[SubCostCodeId], elli.[SubCostCodeId]), -1) AS [SccId],
               SUM(CASE
                     WHEN ili.[BillCreditLineItemId] IS NOT NULL
                          THEN -ISNULL(ili.[Price], 0)
                     WHEN ili.[ExpenseLineItemId] IS NOT NULL AND e.[IsCredit] = 1
                          THEN -ISNULL(ili.[Price], 0)
                     WHEN ili.[BillLineItemId] IS NULL AND ili.[ExpenseLineItemId] IS NULL
                          AND ili.[BillCreditLineItemId] IS NULL AND ili.[EmployeeLaborLineItemId] IS NULL
                          AND ISNULL(ili.[Amount], 0) < 0
                          THEN -ISNULL(ili.[Price], 0)
                     ELSE ISNULL(ili.[Price], 0)
                   END) AS [DrawnPrice]
        FROM dbo.[InvoiceLineItem] ili
        INNER JOIN dbo.[Invoice] i              ON i.[Id]    = ili.[InvoiceId]
        LEFT JOIN dbo.[BillLineItem] bli        ON bli.[Id]  = ili.[BillLineItemId]
        LEFT JOIN dbo.[ExpenseLineItem] eli     ON eli.[Id]  = ili.[ExpenseLineItemId]
        LEFT JOIN dbo.[Expense] e               ON e.[Id]    = eli.[ExpenseId]
        LEFT JOIN dbo.[BillCreditLineItem] bcli ON bcli.[Id] = ili.[BillCreditLineItemId]
        LEFT JOIN dbo.[EmployeeLaborLineItem] elli ON elli.[Id] = ili.[EmployeeLaborLineItemId]
        WHERE i.[ProjectId] = @ProjectId
          AND i.[IsDraft] = 0                   -- strict drawn policy
        GROUP BY ISNULL(COALESCE(ili.[SubCostCodeId], bli.[SubCostCodeId], eli.[SubCostCodeId],
                          bcli.[SubCostCodeId], elli.[SubCostCodeId]), -1)
    ),
    SccKeys AS (
        SELECT [SccId] FROM BudgetAgg
        UNION SELECT [SccId] FROM BillAgg
        UNION SELECT [SccId] FROM ExpenseAgg
        UNION SELECT [SccId] FROM CreditAgg
        UNION SELECT [SccId] FROM ContractLaborAgg
        UNION SELECT [SccId] FROM EmployeeLaborAgg
        UNION SELECT [SccId] FROM DrawnAgg
    )
    SELECT
        NULLIF(k.[SccId], -1) AS [SubCostCodeId],
        scc.[Number]   AS [SubCostCodeNumber],
        scc.[Name]     AS [SubCostCodeName],
        cc.[Id]        AS [CostCodeId],
        cc.[Number]    AS [CostCodeNumber],
        cc.[Name]      AS [CostCodeName],
        CONVERT(DECIMAL(18,2), ISNULL(b.[BudgetAmount], 0))        AS [BudgetAmount],
        CONVERT(DECIMAL(18,2), ISNULL(b.[BudgetPrice], 0))         AS [BudgetPrice],
        CONVERT(DECIMAL(18,2), ISNULL(ba.[BillCost], 0))           AS [BillCost],
        CONVERT(DECIMAL(18,2), ISNULL(ea.[ExpenseCost], 0))        AS [ExpenseCost],
        CONVERT(DECIMAL(18,2), ISNULL(ca.[CreditCost], 0))         AS [BillCreditCost],
        CONVERT(DECIMAL(18,2), ISNULL(cl.[ContractLaborCost], 0))  AS [ContractLaborCost],
        CONVERT(DECIMAL(18,2), ISNULL(el.[EmployeeLaborCost], 0))  AS [EmployeeLaborCost],
        CONVERT(DECIMAL(18,2),
            ISNULL(ba.[BillCost], 0) + ISNULL(ea.[ExpenseCost], 0)
            - ISNULL(ca.[CreditCost], 0)
            + ISNULL(cl.[ContractLaborCost], 0) + ISNULL(el.[EmployeeLaborCost], 0)
        ) AS [ActualCost],
        CONVERT(DECIMAL(18,2), ISNULL(d.[DrawnPrice], 0))          AS [DrawnPrice],
        CONVERT(DECIMAL(18,2),
            ISNULL(b.[BudgetPrice], 0) - ISNULL(d.[DrawnPrice], 0)
        ) AS [RemainingToDraw],
        CONVERT(DECIMAL(18,2),
            ISNULL(b.[BudgetAmount], 0)
            - (ISNULL(ba.[BillCost], 0) + ISNULL(ea.[ExpenseCost], 0)
               - ISNULL(ca.[CreditCost], 0)
               + ISNULL(cl.[ContractLaborCost], 0) + ISNULL(el.[EmployeeLaborCost], 0))
        ) AS [CostVariance],
        CONVERT(DECIMAL(8,2),
            ISNULL(cl.[UnpricedHours], 0) + ISNULL(el.[UnpricedHours], 0)
        ) AS [UnpricedLaborHours]
    FROM SccKeys k
    LEFT JOIN BudgetAgg b          ON b.[SccId]  = k.[SccId]
    LEFT JOIN BillAgg ba           ON ba.[SccId] = k.[SccId]
    LEFT JOIN ExpenseAgg ea        ON ea.[SccId] = k.[SccId]
    LEFT JOIN CreditAgg ca         ON ca.[SccId] = k.[SccId]
    LEFT JOIN ContractLaborAgg cl  ON cl.[SccId] = k.[SccId]
    LEFT JOIN EmployeeLaborAgg el  ON el.[SccId] = k.[SccId]
    LEFT JOIN DrawnAgg d           ON d.[SccId]  = k.[SccId]
    LEFT JOIN dbo.[SubCostCode] scc ON scc.[Id] = k.[SccId]
    LEFT JOIN dbo.[CostCode] cc     ON cc.[Id]  = scc.[CostCodeId]
    WHERE @CanAccess = 1
    ORDER BY CASE WHEN k.[SccId] = -1 THEN 1 ELSE 0 END,  -- Uncategorized last
             cc.[Number], scc.[Number];
END;
GO


-- Per-budget rollups for the list page: contract value (approved revisions)
-- + drawn-to-date (completed invoices, same sign rules as the variance
-- sproc). Actor-scoped inline (Invoice/ContractLabor list pattern) —
-- budget row counts are small (~1 per project), so the per-row UDF is the
-- accepted shape here, NOT over line tables.
CREATE OR ALTER PROCEDURE ReadBudgetListRollups
(
    @ActorUserId        BIGINT = NULL,
    @ActorIsSystemAdmin BIT    = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        b.[Id] AS [BudgetId],
        CONVERT(DECIMAL(18,2), ISNULL(cv.[ContractValue], 0)) AS [ContractValue],
        CONVERT(DECIMAL(18,2), ISNULL(d.[DrawnPrice], 0))     AS [DrawnPrice],
        CONVERT(DECIMAL(18,2),
            ISNULL(cv.[ContractValue], 0) - ISNULL(d.[DrawnPrice], 0)
        ) AS [RemainingToDraw]
    FROM dbo.[Budget] b
    OUTER APPLY (
        SELECT SUM(ISNULL(bli.[Price], 0)) AS [ContractValue]
        FROM dbo.[BudgetLineItem] bli
        INNER JOIN dbo.[BudgetRevision] br ON br.[Id] = bli.[BudgetRevisionId]
        WHERE br.[BudgetId] = b.[Id] AND br.[Status] = 'approved'
    ) cv
    OUTER APPLY (
        SELECT SUM(CASE
                     WHEN ili.[BillCreditLineItemId] IS NOT NULL
                          THEN -ISNULL(ili.[Price], 0)
                     WHEN ili.[ExpenseLineItemId] IS NOT NULL AND e.[IsCredit] = 1
                          THEN -ISNULL(ili.[Price], 0)
                     WHEN ili.[BillLineItemId] IS NULL AND ili.[ExpenseLineItemId] IS NULL
                          AND ili.[BillCreditLineItemId] IS NULL AND ili.[EmployeeLaborLineItemId] IS NULL
                          AND ISNULL(ili.[Amount], 0) < 0
                          THEN -ISNULL(ili.[Price], 0)
                     ELSE ISNULL(ili.[Price], 0)
                   END) AS [DrawnPrice]
        FROM dbo.[InvoiceLineItem] ili
        INNER JOIN dbo.[Invoice] i          ON i.[Id]   = ili.[InvoiceId]
        LEFT JOIN dbo.[ExpenseLineItem] eli ON eli.[Id] = ili.[ExpenseLineItemId]
        LEFT JOIN dbo.[Expense] e           ON e.[Id]   = eli.[ExpenseId]
        WHERE i.[ProjectId] = b.[ProjectId] AND i.[IsDraft] = 0
    ) d
    WHERE b.[Status] <> 'archived'
      AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, b.[ProjectId]) = 1;
END;
GO
