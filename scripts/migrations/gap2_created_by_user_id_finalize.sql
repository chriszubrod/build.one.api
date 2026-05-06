-- Gap 2 finalize — Backfill + DEFAULT + NOT NULL flip on CreatedByUserId
-- across the 30 audit-attribution tables.
-- Per Q2.3 sign-off: backfill all rows to System Admin (Christopher, User.Id=17).
-- Idempotent. Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- =====================================================================
-- 1. Backfill all rows to id=17 (Christopher / System Admin) per Q2.3 = b
-- =====================================================================

UPDATE dbo.[Project] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Bill] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillCredit] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Expense] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Invoice] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ContractLabor] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[TimeEntry] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillCreditLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ExpenseLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[InvoiceLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ContractLaborLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[TimeLog] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Attachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillLineItemAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ExpenseLineItemAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[InvoiceLineItemAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[EmailMessage] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[EmailAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Review] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ReviewStatus] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ReviewEntry] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillFolderRun] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillFolderRunItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Vendor] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Customer] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[SubCostCode] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[CostCode] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[PaymentTerm] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ProjectAddress] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;

PRINT 'Gap 2 backfill complete.';
GO

-- =====================================================================
-- 2. DEFAULT (17) constraints — new inserts populate without code change
-- =====================================================================

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Project_CreatedByUserId') ALTER TABLE [dbo].[Project] ADD CONSTRAINT [DF_Project_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Bill_CreatedByUserId') ALTER TABLE [dbo].[Bill] ADD CONSTRAINT [DF_Bill_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillCredit_CreatedByUserId') ALTER TABLE [dbo].[BillCredit] ADD CONSTRAINT [DF_BillCredit_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Expense_CreatedByUserId') ALTER TABLE [dbo].[Expense] ADD CONSTRAINT [DF_Expense_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Invoice_CreatedByUserId') ALTER TABLE [dbo].[Invoice] ADD CONSTRAINT [DF_Invoice_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ContractLabor_CreatedByUserId') ALTER TABLE [dbo].[ContractLabor] ADD CONSTRAINT [DF_ContractLabor_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_TimeEntry_CreatedByUserId') ALTER TABLE [dbo].[TimeEntry] ADD CONSTRAINT [DF_TimeEntry_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillLineItem_CreatedByUserId') ALTER TABLE [dbo].[BillLineItem] ADD CONSTRAINT [DF_BillLineItem_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillCreditLineItem_CreatedByUserId') ALTER TABLE [dbo].[BillCreditLineItem] ADD CONSTRAINT [DF_BillCreditLineItem_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ExpenseLineItem_CreatedByUserId') ALTER TABLE [dbo].[ExpenseLineItem] ADD CONSTRAINT [DF_ExpenseLineItem_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_InvoiceLineItem_CreatedByUserId') ALTER TABLE [dbo].[InvoiceLineItem] ADD CONSTRAINT [DF_InvoiceLineItem_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ContractLaborLineItem_CreatedByUserId') ALTER TABLE [dbo].[ContractLaborLineItem] ADD CONSTRAINT [DF_ContractLaborLineItem_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_TimeLog_CreatedByUserId') ALTER TABLE [dbo].[TimeLog] ADD CONSTRAINT [DF_TimeLog_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Attachment_CreatedByUserId') ALTER TABLE [dbo].[Attachment] ADD CONSTRAINT [DF_Attachment_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillLineItemAttachment_CreatedByUserId') ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [DF_BillLineItemAttachment_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ExpenseLineItemAttachment_CreatedByUserId') ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD CONSTRAINT [DF_ExpenseLineItemAttachment_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_InvoiceLineItemAttachment_CreatedByUserId') ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD CONSTRAINT [DF_InvoiceLineItemAttachment_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_EmailMessage_CreatedByUserId') ALTER TABLE [dbo].[EmailMessage] ADD CONSTRAINT [DF_EmailMessage_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_EmailAttachment_CreatedByUserId') ALTER TABLE [dbo].[EmailAttachment] ADD CONSTRAINT [DF_EmailAttachment_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Review_CreatedByUserId') ALTER TABLE [dbo].[Review] ADD CONSTRAINT [DF_Review_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ReviewStatus_CreatedByUserId') ALTER TABLE [dbo].[ReviewStatus] ADD CONSTRAINT [DF_ReviewStatus_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ReviewEntry_CreatedByUserId') ALTER TABLE [dbo].[ReviewEntry] ADD CONSTRAINT [DF_ReviewEntry_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillFolderRun_CreatedByUserId') ALTER TABLE [dbo].[BillFolderRun] ADD CONSTRAINT [DF_BillFolderRun_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillFolderRunItem_CreatedByUserId') ALTER TABLE [dbo].[BillFolderRunItem] ADD CONSTRAINT [DF_BillFolderRunItem_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Vendor_CreatedByUserId') ALTER TABLE [dbo].[Vendor] ADD CONSTRAINT [DF_Vendor_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Customer_CreatedByUserId') ALTER TABLE [dbo].[Customer] ADD CONSTRAINT [DF_Customer_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_SubCostCode_CreatedByUserId') ALTER TABLE [dbo].[SubCostCode] ADD CONSTRAINT [DF_SubCostCode_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_CostCode_CreatedByUserId') ALTER TABLE [dbo].[CostCode] ADD CONSTRAINT [DF_CostCode_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_PaymentTerm_CreatedByUserId') ALTER TABLE [dbo].[PaymentTerm] ADD CONSTRAINT [DF_PaymentTerm_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ProjectAddress_CreatedByUserId') ALTER TABLE [dbo].[ProjectAddress] ADD CONSTRAINT [DF_ProjectAddress_CreatedByUserId] DEFAULT (17) FOR [CreatedByUserId];
GO

PRINT 'Gap 2 DEFAULT constraints applied.';
GO

-- =====================================================================
-- 3. Re-backfill: catch any rows created in the gap window between
--    backfill above and DEFAULT constraint application
-- =====================================================================

UPDATE dbo.[Project] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Bill] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillCredit] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Expense] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Invoice] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ContractLabor] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[TimeEntry] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillCreditLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ExpenseLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[InvoiceLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ContractLaborLineItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[TimeLog] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Attachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillLineItemAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ExpenseLineItemAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[InvoiceLineItemAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[EmailMessage] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[EmailAttachment] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Review] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ReviewStatus] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ReviewEntry] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillFolderRun] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[BillFolderRunItem] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Vendor] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[Customer] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[SubCostCode] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[CostCode] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[PaymentTerm] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
UPDATE dbo.[ProjectAddress] SET [CreatedByUserId] = 17 WHERE [CreatedByUserId] IS NULL;
GO

-- =====================================================================
-- 4. NOT NULL flip — drop filtered index, ALTER COLUMN, recreate index
-- =====================================================================

-- Helper macro: drop filtered index, alter column NOT NULL, recreate as
-- normal index. Filtered indexes can't sit on a NOT NULL column anyway,
-- and a plain index is more useful once every row has a value.

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Project') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Project_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Project'))
        DROP INDEX [IX_Project_CreatedByUserId] ON [dbo].[Project];
    ALTER TABLE [dbo].[Project] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Project_CreatedByUserId] ON [dbo].[Project] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Bill') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Bill_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Bill'))
        DROP INDEX [IX_Bill_CreatedByUserId] ON [dbo].[Bill];
    ALTER TABLE [dbo].[Bill] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Bill_CreatedByUserId] ON [dbo].[Bill] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCredit') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCredit_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCredit'))
        DROP INDEX [IX_BillCredit_CreatedByUserId] ON [dbo].[BillCredit];
    ALTER TABLE [dbo].[BillCredit] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillCredit_CreatedByUserId] ON [dbo].[BillCredit] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Expense') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Expense_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Expense'))
        DROP INDEX [IX_Expense_CreatedByUserId] ON [dbo].[Expense];
    ALTER TABLE [dbo].[Expense] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Expense_CreatedByUserId] ON [dbo].[Expense] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Invoice') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Invoice_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Invoice'))
        DROP INDEX [IX_Invoice_CreatedByUserId] ON [dbo].[Invoice];
    ALTER TABLE [dbo].[Invoice] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Invoice_CreatedByUserId] ON [dbo].[Invoice] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLabor') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLabor_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLabor'))
        DROP INDEX [IX_ContractLabor_CreatedByUserId] ON [dbo].[ContractLabor];
    ALTER TABLE [dbo].[ContractLabor] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_ContractLabor_CreatedByUserId] ON [dbo].[ContractLabor] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeEntry') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeEntry_CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeEntry'))
        DROP INDEX [IX_TimeEntry_CreatedByUserId] ON [dbo].[TimeEntry];
    ALTER TABLE [dbo].[TimeEntry] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_TimeEntry_CreatedByUserId] ON [dbo].[TimeEntry] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItem'))
        DROP INDEX [IX_BillLineItem_CreatedByUserId] ON [dbo].[BillLineItem];
    ALTER TABLE [dbo].[BillLineItem] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillLineItem_CreatedByUserId] ON [dbo].[BillLineItem] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCreditLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem'))
        DROP INDEX [IX_BillCreditLineItem_CreatedByUserId] ON [dbo].[BillCreditLineItem];
    ALTER TABLE [dbo].[BillCreditLineItem] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillCreditLineItem_CreatedByUserId] ON [dbo].[BillCreditLineItem] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem'))
        DROP INDEX [IX_ExpenseLineItem_CreatedByUserId] ON [dbo].[ExpenseLineItem];
    ALTER TABLE [dbo].[ExpenseLineItem] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_ExpenseLineItem_CreatedByUserId] ON [dbo].[ExpenseLineItem] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem'))
        DROP INDEX [IX_InvoiceLineItem_CreatedByUserId] ON [dbo].[InvoiceLineItem];
    ALTER TABLE [dbo].[InvoiceLineItem] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_InvoiceLineItem_CreatedByUserId] ON [dbo].[InvoiceLineItem] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLaborLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem'))
        DROP INDEX [IX_ContractLaborLineItem_CreatedByUserId] ON [dbo].[ContractLaborLineItem];
    ALTER TABLE [dbo].[ContractLaborLineItem] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_ContractLaborLineItem_CreatedByUserId] ON [dbo].[ContractLaborLineItem] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeLog') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeLog_CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeLog'))
        DROP INDEX [IX_TimeLog_CreatedByUserId] ON [dbo].[TimeLog];
    ALTER TABLE [dbo].[TimeLog] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_TimeLog_CreatedByUserId] ON [dbo].[TimeLog] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Attachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Attachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Attachment'))
        DROP INDEX [IX_Attachment_CreatedByUserId] ON [dbo].[Attachment];
    ALTER TABLE [dbo].[Attachment] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Attachment_CreatedByUserId] ON [dbo].[Attachment] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItemAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment'))
        DROP INDEX [IX_BillLineItemAttachment_CreatedByUserId] ON [dbo].[BillLineItemAttachment];
    ALTER TABLE [dbo].[BillLineItemAttachment] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillLineItemAttachment_CreatedByUserId] ON [dbo].[BillLineItemAttachment] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItemAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment'))
        DROP INDEX [IX_ExpenseLineItemAttachment_CreatedByUserId] ON [dbo].[ExpenseLineItemAttachment];
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_ExpenseLineItemAttachment_CreatedByUserId] ON [dbo].[ExpenseLineItemAttachment] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItemAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment'))
        DROP INDEX [IX_InvoiceLineItemAttachment_CreatedByUserId] ON [dbo].[InvoiceLineItemAttachment];
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_InvoiceLineItemAttachment_CreatedByUserId] ON [dbo].[InvoiceLineItemAttachment] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailMessage') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailMessage_CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailMessage'))
        DROP INDEX [IX_EmailMessage_CreatedByUserId] ON [dbo].[EmailMessage];
    ALTER TABLE [dbo].[EmailMessage] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_EmailMessage_CreatedByUserId] ON [dbo].[EmailMessage] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailAttachment'))
        DROP INDEX [IX_EmailAttachment_CreatedByUserId] ON [dbo].[EmailAttachment];
    ALTER TABLE [dbo].[EmailAttachment] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_EmailAttachment_CreatedByUserId] ON [dbo].[EmailAttachment] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Review') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Review_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Review'))
        DROP INDEX [IX_Review_CreatedByUserId] ON [dbo].[Review];
    ALTER TABLE [dbo].[Review] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Review_CreatedByUserId] ON [dbo].[Review] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewStatus') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewStatus_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewStatus'))
        DROP INDEX [IX_ReviewStatus_CreatedByUserId] ON [dbo].[ReviewStatus];
    ALTER TABLE [dbo].[ReviewStatus] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_ReviewStatus_CreatedByUserId] ON [dbo].[ReviewStatus] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewEntry') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewEntry_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewEntry'))
        DROP INDEX [IX_ReviewEntry_CreatedByUserId] ON [dbo].[ReviewEntry];
    ALTER TABLE [dbo].[ReviewEntry] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_ReviewEntry_CreatedByUserId] ON [dbo].[ReviewEntry] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRun') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRun_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRun'))
        DROP INDEX [IX_BillFolderRun_CreatedByUserId] ON [dbo].[BillFolderRun];
    ALTER TABLE [dbo].[BillFolderRun] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillFolderRun_CreatedByUserId] ON [dbo].[BillFolderRun] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRunItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem'))
        DROP INDEX [IX_BillFolderRunItem_CreatedByUserId] ON [dbo].[BillFolderRunItem];
    ALTER TABLE [dbo].[BillFolderRunItem] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillFolderRunItem_CreatedByUserId] ON [dbo].[BillFolderRunItem] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Vendor') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Vendor_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Vendor'))
        DROP INDEX [IX_Vendor_CreatedByUserId] ON [dbo].[Vendor];
    ALTER TABLE [dbo].[Vendor] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Vendor_CreatedByUserId] ON [dbo].[Vendor] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Customer') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Customer_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Customer'))
        DROP INDEX [IX_Customer_CreatedByUserId] ON [dbo].[Customer];
    ALTER TABLE [dbo].[Customer] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_Customer_CreatedByUserId] ON [dbo].[Customer] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.SubCostCode') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_SubCostCode_CreatedByUserId' AND object_id=OBJECT_ID('dbo.SubCostCode'))
        DROP INDEX [IX_SubCostCode_CreatedByUserId] ON [dbo].[SubCostCode];
    ALTER TABLE [dbo].[SubCostCode] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_SubCostCode_CreatedByUserId] ON [dbo].[SubCostCode] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.CostCode') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_CostCode_CreatedByUserId' AND object_id=OBJECT_ID('dbo.CostCode'))
        DROP INDEX [IX_CostCode_CreatedByUserId] ON [dbo].[CostCode];
    ALTER TABLE [dbo].[CostCode] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_CostCode_CreatedByUserId] ON [dbo].[CostCode] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.PaymentTerm') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_PaymentTerm_CreatedByUserId' AND object_id=OBJECT_ID('dbo.PaymentTerm'))
        DROP INDEX [IX_PaymentTerm_CreatedByUserId] ON [dbo].[PaymentTerm];
    ALTER TABLE [dbo].[PaymentTerm] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_PaymentTerm_CreatedByUserId] ON [dbo].[PaymentTerm] ([CreatedByUserId]);
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ProjectAddress') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ProjectAddress_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ProjectAddress'))
        DROP INDEX [IX_ProjectAddress_CreatedByUserId] ON [dbo].[ProjectAddress];
    ALTER TABLE [dbo].[ProjectAddress] ALTER COLUMN [CreatedByUserId] BIGINT NOT NULL;
    CREATE INDEX [IX_ProjectAddress_CreatedByUserId] ON [dbo].[ProjectAddress] ([CreatedByUserId]);
END
GO

PRINT 'Gap 2 NOT NULL flip complete — 30 tables.';
