-- Migration: U-225 -- close the qbo.* mapping-table FK gap (11 tables, 0 FK today)
-- Purpose: enforce referential integrity on the 11 qbo.* mapping tables flagged by
--          docs/audit_qbo_integration_2026_08_07.md:461 as carrying no FK at all.
--          Every constraint below is ON DELETE NO ACTION on BOTH sides -- deliberately
--          NOT the Purchase-family precedent (add_fk_constraints_to_mapping_tables.sql,
--          which CASCADEs on the qbo-staging side). NO ACTION matches this codebase's
--          standing DBA convention ("FK has no CASCADE DELETE -- deletes are ordered
--          in app code") and avoids DB-level CASCADE becoming a silent trap once
--          U-226's app-level qbo.* mapping cleanup ships (2026-08-16 EM decision,
--          Chris confirmed explicitly).
-- Orphan counts measured live in prod 2026-08-16 (read-only survey, U-225):
--   qbo-staging side: 0 orphans on ALL 11 tables today -- these columns would
--   validate cleanly even WITH CHECK.
--   dbo side: pre-existing orphans on 6 of 11 tables (BillBill=1,
--   BillLineItemBillLine=6, InvoiceInvoice=8, InvoiceLineItemInvoiceLine=122,
--   ItemCostCode=2, AttachableAttachment=2 -- evidence for U-226; the first four
--   numbers match U-238c's independently-found 137 dangling rows exactly,
--   1+6+8+122=137, the CostCode/Attachment orphans are 2 more U-225 surfaced).
--   WITH NOCHECK is used uniformly below (both sides) so this migration applies
--   cleanly regardless of when the dbo-side cleanup lands. Once those rows are
--   cleared, re-run `ALTER TABLE [qbo].[<table>] WITH CHECK CHECK CONSTRAINT
--   [<name>];` per constraint to mark it TRUSTED for the query optimizer -- not
--   done here, follow-up for whoever applies this.
-- Additive only, NOT EXISTS-guarded by constraint name, never drops or recreates
-- a table. COMMITTED, NOT APPLIED -- prod apply is a separate, explicitly-approved
-- deploy step (this unit's mandate is the migration file only).
-- Run with: python scripts/run_sql.py scripts/migrations/u225_qbo_mapping_fk_gaps.sql

-- ============================================================================
-- 1. qbo.BillBill
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillBill_QboBill')
BEGIN
    ALTER TABLE [qbo].[BillBill] WITH NOCHECK
    ADD CONSTRAINT [FK_BillBill_QboBill]
        FOREIGN KEY ([QboBillId]) REFERENCES [qbo].[Bill]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_BillBill_QboBill (NO ACTION)';
END
ELSE
    PRINT 'FK_BillBill_QboBill already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillBill_Bill')
BEGIN
    ALTER TABLE [qbo].[BillBill] WITH NOCHECK
    ADD CONSTRAINT [FK_BillBill_Bill]
        FOREIGN KEY ([BillId]) REFERENCES [dbo].[Bill]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_BillBill_Bill (NO ACTION) -- 1 pre-existing orphan row measured 2026-08-16';
END
ELSE
    PRINT 'FK_BillBill_Bill already exists';
GO

-- ============================================================================
-- 2. qbo.BillLineItemBillLine
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillLineItemBillLine_QboBillLine')
BEGIN
    ALTER TABLE [qbo].[BillLineItemBillLine] WITH NOCHECK
    ADD CONSTRAINT [FK_BillLineItemBillLine_QboBillLine]
        FOREIGN KEY ([QboBillLineId]) REFERENCES [qbo].[BillLine]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_BillLineItemBillLine_QboBillLine (NO ACTION)';
END
ELSE
    PRINT 'FK_BillLineItemBillLine_QboBillLine already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_BillLineItemBillLine_BillLineItem')
BEGIN
    ALTER TABLE [qbo].[BillLineItemBillLine] WITH NOCHECK
    ADD CONSTRAINT [FK_BillLineItemBillLine_BillLineItem]
        FOREIGN KEY ([BillLineItemId]) REFERENCES [dbo].[BillLineItem]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_BillLineItemBillLine_BillLineItem (NO ACTION) -- 6 pre-existing orphan rows measured 2026-08-16';
END
ELSE
    PRINT 'FK_BillLineItemBillLine_BillLineItem already exists';
GO

-- ============================================================================
-- 3. qbo.InvoiceInvoice
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceInvoice_QboInvoice')
BEGIN
    ALTER TABLE [qbo].[InvoiceInvoice] WITH NOCHECK
    ADD CONSTRAINT [FK_InvoiceInvoice_QboInvoice]
        FOREIGN KEY ([QboInvoiceId]) REFERENCES [qbo].[Invoice]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_InvoiceInvoice_QboInvoice (NO ACTION)';
END
ELSE
    PRINT 'FK_InvoiceInvoice_QboInvoice already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceInvoice_Invoice')
BEGIN
    ALTER TABLE [qbo].[InvoiceInvoice] WITH NOCHECK
    ADD CONSTRAINT [FK_InvoiceInvoice_Invoice]
        FOREIGN KEY ([InvoiceId]) REFERENCES [dbo].[Invoice]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_InvoiceInvoice_Invoice (NO ACTION) -- 8 pre-existing orphan rows measured 2026-08-16';
END
ELSE
    PRINT 'FK_InvoiceInvoice_Invoice already exists';
GO

-- ============================================================================
-- 4. qbo.InvoiceLineItemInvoiceLine
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItemInvoiceLine_QboInvoiceLine')
BEGIN
    ALTER TABLE [qbo].[InvoiceLineItemInvoiceLine] WITH NOCHECK
    ADD CONSTRAINT [FK_InvoiceLineItemInvoiceLine_QboInvoiceLine]
        FOREIGN KEY ([QboInvoiceLineId]) REFERENCES [qbo].[InvoiceLine]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_InvoiceLineItemInvoiceLine_QboInvoiceLine (NO ACTION)';
END
ELSE
    PRINT 'FK_InvoiceLineItemInvoiceLine_QboInvoiceLine already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItemInvoiceLine_InvoiceLineItem')
BEGIN
    ALTER TABLE [qbo].[InvoiceLineItemInvoiceLine] WITH NOCHECK
    ADD CONSTRAINT [FK_InvoiceLineItemInvoiceLine_InvoiceLineItem]
        FOREIGN KEY ([InvoiceLineItemId]) REFERENCES [dbo].[InvoiceLineItem]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_InvoiceLineItemInvoiceLine_InvoiceLineItem (NO ACTION) -- 122 pre-existing orphan rows measured 2026-08-16';
END
ELSE
    PRINT 'FK_InvoiceLineItemInvoiceLine_InvoiceLineItem already exists';
GO

-- ============================================================================
-- 5. qbo.CustomerCustomer
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_CustomerCustomer_QboCustomer')
BEGIN
    ALTER TABLE [qbo].[CustomerCustomer] WITH NOCHECK
    ADD CONSTRAINT [FK_CustomerCustomer_QboCustomer]
        FOREIGN KEY ([QboCustomerId]) REFERENCES [qbo].[Customer]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_CustomerCustomer_QboCustomer (NO ACTION)';
END
ELSE
    PRINT 'FK_CustomerCustomer_QboCustomer already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_CustomerCustomer_Customer')
BEGIN
    ALTER TABLE [qbo].[CustomerCustomer] WITH NOCHECK
    ADD CONSTRAINT [FK_CustomerCustomer_Customer]
        FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customer]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_CustomerCustomer_Customer (NO ACTION) -- 0 orphans measured 2026-08-16';
END
ELSE
    PRINT 'FK_CustomerCustomer_Customer already exists';
GO

-- ============================================================================
-- 6. qbo.CustomerProject
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_CustomerProject_QboCustomer')
BEGIN
    ALTER TABLE [qbo].[CustomerProject] WITH NOCHECK
    ADD CONSTRAINT [FK_CustomerProject_QboCustomer]
        FOREIGN KEY ([QboCustomerId]) REFERENCES [qbo].[Customer]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_CustomerProject_QboCustomer (NO ACTION)';
END
ELSE
    PRINT 'FK_CustomerProject_QboCustomer already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_CustomerProject_Project')
BEGIN
    ALTER TABLE [qbo].[CustomerProject] WITH NOCHECK
    ADD CONSTRAINT [FK_CustomerProject_Project]
        FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_CustomerProject_Project (NO ACTION) -- 0 orphans measured 2026-08-16';
END
ELSE
    PRINT 'FK_CustomerProject_Project already exists';
GO

-- ============================================================================
-- 7. qbo.VendorVendor
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorVendor_QboVendor')
BEGIN
    ALTER TABLE [qbo].[VendorVendor] WITH NOCHECK
    ADD CONSTRAINT [FK_VendorVendor_QboVendor]
        FOREIGN KEY ([QboVendorId]) REFERENCES [qbo].[Vendor]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_VendorVendor_QboVendor (NO ACTION)';
END
ELSE
    PRINT 'FK_VendorVendor_QboVendor already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorVendor_Vendor')
BEGIN
    ALTER TABLE [qbo].[VendorVendor] WITH NOCHECK
    ADD CONSTRAINT [FK_VendorVendor_Vendor]
        FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_VendorVendor_Vendor (NO ACTION) -- 0 orphans measured 2026-08-16';
END
ELSE
    PRINT 'FK_VendorVendor_Vendor already exists';
GO

-- ============================================================================
-- 8. qbo.ItemCostCode
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ItemCostCode_QboItem')
BEGIN
    ALTER TABLE [qbo].[ItemCostCode] WITH NOCHECK
    ADD CONSTRAINT [FK_ItemCostCode_QboItem]
        FOREIGN KEY ([QboItemId]) REFERENCES [qbo].[Item]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_ItemCostCode_QboItem (NO ACTION)';
END
ELSE
    PRINT 'FK_ItemCostCode_QboItem already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ItemCostCode_CostCode')
BEGIN
    ALTER TABLE [qbo].[ItemCostCode] WITH NOCHECK
    ADD CONSTRAINT [FK_ItemCostCode_CostCode]
        FOREIGN KEY ([CostCodeId]) REFERENCES [dbo].[CostCode]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_ItemCostCode_CostCode (NO ACTION) -- 2 pre-existing orphan rows measured 2026-08-16';
END
ELSE
    PRINT 'FK_ItemCostCode_CostCode already exists';
GO

-- ============================================================================
-- 9. qbo.ItemSubCostCode
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ItemSubCostCode_QboItem')
BEGIN
    ALTER TABLE [qbo].[ItemSubCostCode] WITH NOCHECK
    ADD CONSTRAINT [FK_ItemSubCostCode_QboItem]
        FOREIGN KEY ([QboItemId]) REFERENCES [qbo].[Item]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_ItemSubCostCode_QboItem (NO ACTION)';
END
ELSE
    PRINT 'FK_ItemSubCostCode_QboItem already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ItemSubCostCode_SubCostCode')
BEGIN
    ALTER TABLE [qbo].[ItemSubCostCode] WITH NOCHECK
    ADD CONSTRAINT [FK_ItemSubCostCode_SubCostCode]
        FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_ItemSubCostCode_SubCostCode (NO ACTION) -- 0 orphans measured 2026-08-16';
END
ELSE
    PRINT 'FK_ItemSubCostCode_SubCostCode already exists';
GO

-- ============================================================================
-- 10. qbo.TermPaymentTerm
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_TermPaymentTerm_QboTerm')
BEGIN
    ALTER TABLE [qbo].[TermPaymentTerm] WITH NOCHECK
    ADD CONSTRAINT [FK_TermPaymentTerm_QboTerm]
        FOREIGN KEY ([QboTermId]) REFERENCES [qbo].[Term]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_TermPaymentTerm_QboTerm (NO ACTION)';
END
ELSE
    PRINT 'FK_TermPaymentTerm_QboTerm already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_TermPaymentTerm_PaymentTerm')
BEGIN
    ALTER TABLE [qbo].[TermPaymentTerm] WITH NOCHECK
    ADD CONSTRAINT [FK_TermPaymentTerm_PaymentTerm]
        FOREIGN KEY ([PaymentTermId]) REFERENCES [dbo].[PaymentTerm]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_TermPaymentTerm_PaymentTerm (NO ACTION) -- 0 orphans measured 2026-08-16';
END
ELSE
    PRINT 'FK_TermPaymentTerm_PaymentTerm already exists';
GO

-- ============================================================================
-- 11. qbo.AttachableAttachment
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AttachableAttachment_QboAttachable')
BEGIN
    ALTER TABLE [qbo].[AttachableAttachment] WITH NOCHECK
    ADD CONSTRAINT [FK_AttachableAttachment_QboAttachable]
        FOREIGN KEY ([QboAttachableId]) REFERENCES [qbo].[Attachable]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_AttachableAttachment_QboAttachable (NO ACTION)';
END
ELSE
    PRINT 'FK_AttachableAttachment_QboAttachable already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_AttachableAttachment_Attachment')
BEGIN
    ALTER TABLE [qbo].[AttachableAttachment] WITH NOCHECK
    ADD CONSTRAINT [FK_AttachableAttachment_Attachment]
        FOREIGN KEY ([AttachmentId]) REFERENCES [dbo].[Attachment]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_AttachableAttachment_Attachment (NO ACTION) -- 2 pre-existing orphan rows measured 2026-08-16';
END
ELSE
    PRINT 'FK_AttachableAttachment_Attachment already exists';
GO
