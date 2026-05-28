-- =============================================================================
-- 2026-05-28 — extend Review to support ContractLabor as a parent type.
--
-- Mirrors the existing Bill/Expense/BillCredit/Invoice pattern: nullable FK
-- column + filtered index + CHECK constraint requiring exactly one parent
-- FK to be non-null.
--
-- Adapts the polymorphic review pipeline for the AP/billing review of
-- ContractLabor rows (PMs/Owners approving rate/markup/SCC decisions before
-- the row transitions to 'ready' and gets picked up by Generate Bills).
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- 1. Column ---------------------------------------------------------------
IF COL_LENGTH('dbo.[Review]', 'ContractLaborId') IS NULL
    ALTER TABLE dbo.[Review] ADD [ContractLaborId] BIGINT NULL;
GO


-- 2. Foreign key ----------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Review_ContractLabor')
   AND OBJECT_ID('dbo.[ContractLabor]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.[Review]
    ADD CONSTRAINT [FK_Review_ContractLabor]
        FOREIGN KEY ([ContractLaborId]) REFERENCES dbo.[ContractLabor]([Id]);
END
GO


-- 3. Filtered index -------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_ContractLaborId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_ContractLaborId]
        ON dbo.[Review] ([ContractLaborId])
        WHERE [ContractLaborId] IS NOT NULL;
END
GO


-- 4. CHECK constraint -----------------------------------------------------
-- Drop the old single-parent CHECK (which only counts Bill/Expense/BillCredit/
-- Invoice) and recreate it to include ContractLaborId.
IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Review_OneParent')
BEGIN
    ALTER TABLE dbo.[Review] DROP CONSTRAINT [CK_Review_OneParent];
END
GO

ALTER TABLE dbo.[Review]
ADD CONSTRAINT [CK_Review_OneParent] CHECK (
    (CASE WHEN [BillId]          IS NOT NULL THEN 1 ELSE 0 END) +
    (CASE WHEN [ExpenseId]       IS NOT NULL THEN 1 ELSE 0 END) +
    (CASE WHEN [BillCreditId]    IS NOT NULL THEN 1 ELSE 0 END) +
    (CASE WHEN [InvoiceId]       IS NOT NULL THEN 1 ELSE 0 END) +
    (CASE WHEN [ContractLaborId] IS NOT NULL THEN 1 ELSE 0 END) = 1
);
GO


PRINT 'Review now supports ContractLabor as a parent type.';
