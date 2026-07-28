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

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-162, 2026-07-28) — sproc body removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Sort ContractLaborLineItem reads by source TimeLog ClockIn (OUTER APPLY ordering).
--
-- The canonical definition of this sproc now lives in exactly ONE place:
--   entities/contract_labor/sql/dbo.contract_labor.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.ReadContractLaborLineItemsByContractLaborId
--
-- Drift: this file was the LIVE side (SET NOCOUNT ON + the OUTER APPLY TimeLog
-- clock-in ordering) — the base was STALE (BEGIN TRANSACTION, ORDER BY [Id])
-- and has been reconciled to this file's form verbatim.
--
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------
GO

PRINT 'SUPERSEDED (U-162): ReadContractLaborLineItemsByContractLaborId is canonical in entities/contract_labor/sql/dbo.contract_labor.sql; no sproc applied here.';
