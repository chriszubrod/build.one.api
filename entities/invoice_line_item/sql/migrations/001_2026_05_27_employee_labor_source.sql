-- =============================================================================
-- 2026-05-27 — Phase 3: Invoice source for EmployeeLabor.  (historical migration;
-- sproc bodies superseded — see U-150 banner below. The DDL below is still live.)
--
-- Adds InvoiceLineItem.EmployeeLaborLineItemId nullable FK so the polymorphic
-- source set grows from {Bill, Expense, BillCredit, Manual} to add
-- {EmployeeLabor}. SourceType has no CHECK constraint today so the new value
-- 'EmployeeLabor' is purely a Python convention — no constraint to update.
--
-- Re-issues Create/Read/Update sprocs with the new column threaded through.
-- DeleteInvoiceLineItemById OUTPUT NOT re-issued — its returned shape is
-- only used to confirm deletion; missing the new column doesn't break callers.
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- Column addition + FK + index --------------------------------------------------
IF COL_LENGTH('dbo.[InvoiceLineItem]', 'EmployeeLaborLineItemId') IS NULL
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [EmployeeLaborLineItemId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItem_EmployeeLaborLineItem')
   AND OBJECT_ID('dbo.[EmployeeLaborLineItem]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem]
    ADD CONSTRAINT [FK_InvoiceLineItem_EmployeeLaborLineItem]
        FOREIGN KEY ([EmployeeLaborLineItemId]) REFERENCES [dbo].[EmployeeLaborLineItem]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_EmployeeLaborLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    CREATE INDEX [IX_InvoiceLineItem_EmployeeLaborLineItemId]
        ON [dbo].[InvoiceLineItem] ([EmployeeLaborLineItemId]);
END
GO


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-150, 2026-07-24) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Phase 3 — EmployeeLabor invoice source
--   Add InvoiceLineItem.EmployeeLaborLineItemId nullable FK and re-issue
--   Create/Read/Update sprocs with the new column threaded through.
--   Idempotent (CREATE OR ALTER).
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/invoice_line_item/sql/dbo.invoice_line_item.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.CreateInvoiceLineItem
--   dbo.ReadInvoiceLineItems
--   dbo.ReadInvoiceLineItemById
--   dbo.ReadInvoiceLineItemByPublicId
--   dbo.ReadInvoiceLineItemsByInvoiceId
--   dbo.UpdateInvoiceLineItemById
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — this migration was NEVER APPLIED to prod: the base file was
-- made canonical and applied+verified on 2026-07-06 (commit be2a877) after the
-- WVA-17/WVA-18 incident, and the bodies formerly here were STALE — they
-- declared @EmployeeLaborLineItemId as a REQUIRED param (BIGINT NULL) where
-- the live base declares it OPTIONAL (BIGINT = NULL), so re-running this file
-- would have downgraded prod's Create and Update paths. The DDL section ABOVE
-- this banner (EmployeeLaborLineItemId column + FK + index) is RETAINED and
-- still authoritative/idempotent.
-- ---------------------------------------------------------------------------

PRINT 'InvoiceLineItem EmployeeLabor source migration applied.';
