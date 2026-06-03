-- =============================================================================
-- 2026-06-03 — Sort ContractLaborLineItem reads by source TimeLog ClockIn
-- =============================================================================
--
-- The aggregator (AggregateTimeEntryOnSubmit) inserts line items per
-- bucket as the cursor iterates @Buckets. The @Buckets table is a
-- DECLARE TABLE with no ORDER BY on the SELECT, so cursor order is
-- effectively driven by SQL Server's choice (often hash- or
-- value-ordered by ProjectId). The result: line-item Ids are NOT in
-- the chronological order the worker actually logged the projects in.
--
-- Example — CL.516 (Ricky Moreno, 2026-05-27, 4 projects):
--   liId   ProjectId   ClockIn (from source TimeLog)
--   369    13          15:06   ← inserted first
--   370    48          10:46
--   371    76          09:31
--   372    145         07:28   ← inserted last, but earliest
--
-- React Edit + View pages order line items by Id (sproc ORDER BY Id),
-- which is reverse-chronological for this case.
--
-- Fix: ORDER BY the earliest ClockIn of matching TimeLogs (joined via
-- the line item's SourceTimeEntryId + ProjectId). Manual rows and rows
-- whose source TimeLogs no longer exist fall back to Id ASC. No data
-- migration needed — the existing rows just re-sort on read.
-- =============================================================================

GO

CREATE OR ALTER PROCEDURE ReadContractLaborLineItemsByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        li.[Id],
        li.[PublicId],
        li.[RowVersion],
        CONVERT(VARCHAR(19), li.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), li.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        li.[ContractLaborId],
        CONVERT(VARCHAR(10), li.[LineDate], 120) AS [LineDate],
        li.[ProjectId],
        li.[SubCostCodeId],
        li.[Description],
        li.[Hours],
        li.[Rate],
        li.[Markup],
        li.[Price],
        li.[IsBillable],
        li.[IsOverhead],
        li.[BillLineItemId]
    FROM dbo.[ContractLaborLineItem] li
    OUTER APPLY (
        SELECT MIN(tl.[ClockIn]) AS MinClockIn
        FROM dbo.[TimeLog] tl
        WHERE tl.[TimeEntryId] = li.[SourceTimeEntryId]
          AND ((li.[ProjectId] IS NULL AND tl.[ProjectId] IS NULL)
               OR tl.[ProjectId] = li.[ProjectId])
          AND (tl.[LogType] IS NULL OR tl.[LogType] = 'work')
    ) ord
    WHERE li.[ContractLaborId] = @ContractLaborId
    ORDER BY
        li.[LineDate] ASC,
        ord.MinClockIn ASC,   -- NULLs (manual rows / no matching TimeLog) sort first; then Id breaks ties
        li.[Id] ASC;
END;
GO

PRINT 'ReadContractLaborLineItemsByContractLaborId re-issued: ORDER BY source TimeLog ClockIn.';
