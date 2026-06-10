-- =============================================================================
-- 2026-06-09 — Recompute parent ContractLabor aggregates from child line items.
--
-- Called by the API after PUT /api/v1/contract-labor/{id}/bill applies the
-- caller's line-item edits, so the list page (/labor/list) and any downstream
-- consumer see the up-to-date parent totals — not the TimeEntry-submit
-- snapshot that previously stuck around even after PMs edited line items.
--
-- Recomputed fields:
--   TotalHours  = SUM(ContractLaborLineItem.Hours) over ALL lines
--                 (billable + non-billable — represents hours worked)
--   TotalAmount = SUM(Price) over BILLABLE lines only (post-markup, billed $)
--   HourlyRate  = weighted-avg rate over billable lines with non-null Rate:
--                 SUM(Hours * Rate) / SUM(Hours)
--   Markup      = effective markup fraction:
--                 (TotalAmount - SUM(Hours * Rate)) / SUM(Hours * Rate)
--
-- No row_version check — this is a server-internal recompute triggered AFTER
-- the line-item changes have already been committed under their own
-- row_versions. The UPDATE bumps the parent ROWVERSION; the sproc returns
-- the post-recompute parent row so the API can thread the fresh row_version
-- to the client.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =============================================================================

CREATE OR ALTER PROCEDURE dbo.UpdateContractLaborAggregates
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @TotalHours        DECIMAL(6,2);
    DECLARE @TotalAmount       DECIMAL(18,2);
    DECLARE @HourlyRate        DECIMAL(18,4);
    DECLARE @Markup            DECIMAL(18,4);
    DECLARE @BillableHours     DECIMAL(18,4);
    DECLARE @BillablePreMarkup DECIMAL(18,2);

    -- Total hours across all lines (worked time, billable + non-billable).
    SELECT @TotalHours = ISNULL(SUM(ISNULL([Hours], 0)), 0)
    FROM dbo.[ContractLaborLineItem]
    WHERE [ContractLaborId] = @Id;

    -- Total billed amount (post-markup) over billable lines.
    SELECT @TotalAmount = ISNULL(SUM(ISNULL([Price], 0)), 0)
    FROM dbo.[ContractLaborLineItem]
    WHERE [ContractLaborId] = @Id
      AND [IsBillable] = 1;

    -- Weighted-average rate + effective markup over billable lines with both
    -- Hours and Rate non-null. Lines missing either are excluded from the
    -- rate computation so they don't pull the weighted average toward 0.
    SELECT
        @BillableHours     = ISNULL(SUM([Hours]), 0),
        @BillablePreMarkup = ISNULL(SUM([Hours] * [Rate]), 0)
    FROM dbo.[ContractLaborLineItem]
    WHERE [ContractLaborId] = @Id
      AND [IsBillable] = 1
      AND [Hours] IS NOT NULL
      AND [Rate]  IS NOT NULL;

    SET @HourlyRate = CASE WHEN @BillableHours > 0
                           THEN @BillablePreMarkup / @BillableHours
                           ELSE NULL END;

    SET @Markup = CASE WHEN @BillablePreMarkup > 0
                       THEN (@TotalAmount - @BillablePreMarkup) / @BillablePreMarkup
                       ELSE NULL END;

    -- Update the parent. No row_version check — the API serializes this AFTER
    -- the bill-info update and child line-item commits, and returns the
    -- post-update row to the client so retries see fresh row_version.
    UPDATE dbo.[ContractLabor]
    SET
        [TotalHours]       = @TotalHours,
        [TotalAmount]      = @TotalAmount,
        [HourlyRate]       = @HourlyRate,
        [Markup]           = @Markup,
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @Id;

    -- Return the updated parent row for the caller to thread to the client.
    SELECT *
    FROM dbo.[ContractLabor]
    WHERE [Id] = @Id;
END;
GO


PRINT 'UpdateContractLaborAggregates created.';
