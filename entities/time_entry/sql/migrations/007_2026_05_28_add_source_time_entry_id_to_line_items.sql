-- =============================================================================
-- 2026-05-28 — Phase 0 fix: add SourceTimeEntryId to the labor line item tables
-- so AggregateTimeEntryOnSubmit can upsert lineage-tagged line items idempotently.
--
-- Background. The original WIP sproc (migrations 001 + 002) created only PARENT
-- ContractLabor / EmployeeLabor rows. bill_service.generate_bills_for_vendor
-- walks ContractLaborLineItem rows to produce BillLineItems and skips parents
-- with no children — so TT-sourced aggregation would silently produce
-- unbillable rows.
--
-- The fix (this file + a sproc revision) writes one line item per (parent,
-- project) bucket, tagged with SourceTimeEntryId for idempotent re-aggregation
-- on reject→edit→resubmit cycles.
--
-- Two ALTERs (idempotent), two FKs to dbo.TimeEntry(Id), two filtered indexes.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- ContractLaborLineItem.SourceTimeEntryId -------------------------------------

IF OBJECT_ID('dbo.[ContractLaborLineItem]', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.[ContractLaborLineItem]', 'SourceTimeEntryId') IS NULL
BEGIN
    ALTER TABLE dbo.[ContractLaborLineItem] ADD [SourceTimeEntryId] BIGINT NULL;
END
GO

IF OBJECT_ID('dbo.[ContractLaborLineItem]', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.[TimeEntry]', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ContractLaborLineItem_SourceTimeEntry')
BEGIN
    ALTER TABLE dbo.[ContractLaborLineItem]
    ADD CONSTRAINT FK_ContractLaborLineItem_SourceTimeEntry
        FOREIGN KEY ([SourceTimeEntryId]) REFERENCES dbo.[TimeEntry]([Id]);
END
GO

IF OBJECT_ID('dbo.[ContractLaborLineItem]', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLaborLineItem_SourceTimeEntryId' AND object_id = OBJECT_ID('dbo.[ContractLaborLineItem]'))
BEGIN
    CREATE INDEX IX_ContractLaborLineItem_SourceTimeEntryId
        ON dbo.[ContractLaborLineItem] ([SourceTimeEntryId])
        WHERE [SourceTimeEntryId] IS NOT NULL;
END
GO


-- EmployeeLaborLineItem.SourceTimeEntryId -------------------------------------

IF OBJECT_ID('dbo.[EmployeeLaborLineItem]', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.[EmployeeLaborLineItem]', 'SourceTimeEntryId') IS NULL
BEGIN
    ALTER TABLE dbo.[EmployeeLaborLineItem] ADD [SourceTimeEntryId] BIGINT NULL;
END
GO

IF OBJECT_ID('dbo.[EmployeeLaborLineItem]', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.[TimeEntry]', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLaborLineItem_SourceTimeEntry')
BEGIN
    ALTER TABLE dbo.[EmployeeLaborLineItem]
    ADD CONSTRAINT FK_EmployeeLaborLineItem_SourceTimeEntry
        FOREIGN KEY ([SourceTimeEntryId]) REFERENCES dbo.[TimeEntry]([Id]);
END
GO

IF OBJECT_ID('dbo.[EmployeeLaborLineItem]', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmployeeLaborLineItem_SourceTimeEntryId' AND object_id = OBJECT_ID('dbo.[EmployeeLaborLineItem]'))
BEGIN
    CREATE INDEX IX_EmployeeLaborLineItem_SourceTimeEntryId
        ON dbo.[EmployeeLaborLineItem] ([SourceTimeEntryId])
        WHERE [SourceTimeEntryId] IS NOT NULL;
END
GO

PRINT 'Added SourceTimeEntryId lineage column to ContractLaborLineItem + EmployeeLaborLineItem.';
