-- =============================================================================
-- 2026-05-27 — Phase 5: schema rationalization.
--
-- Adds dbo.ContractLabor.SourceTimeEntryId so vendor-path aggregation has the
-- same lineage as EmployeeLabor.SourceTimeEntryId. Lets the Phase 5b edit-lock
-- guard discover "which TimeEntry produced this ContractLabor row" cheaply.
--
-- Excel-import-specific columns (EmployeeName raw, JobName, TimeIn/TimeOut,
-- BreakTime, RegularHours, OvertimeHours, ImportBatchId, SourceFile, SourceRow)
-- are NOT being removed — they stay nullable forever. Per the schema sketch's
-- "leave nullable, mark as legacy" decision: TimeTracking-sourced ContractLabor
-- rows leave them NULL; Excel-import rows still populate them.
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


IF COL_LENGTH('dbo.[ContractLabor]', 'SourceTimeEntryId') IS NULL
    ALTER TABLE [dbo].[ContractLabor] ADD [SourceTimeEntryId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ContractLabor_SourceTimeEntry')
   AND OBJECT_ID('dbo.[TimeEntry]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[ContractLabor]
    ADD CONSTRAINT [FK_ContractLabor_SourceTimeEntry]
        FOREIGN KEY ([SourceTimeEntryId]) REFERENCES [dbo].[TimeEntry]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_SourceTimeEntryId' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
    CREATE INDEX [IX_ContractLabor_SourceTimeEntryId]
        ON [dbo].[ContractLabor] ([SourceTimeEntryId])
        WHERE [SourceTimeEntryId] IS NOT NULL;
END
GO

PRINT 'ContractLabor.SourceTimeEntryId migration applied.';
