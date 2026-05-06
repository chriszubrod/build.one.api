-- Phase 5 — Multi-tenant scoping. Schema additions only.
-- Adds CompanyId BIGINT NULL FK Company + non-unique index to 23
-- tenant-data tables. NULL during the deploy window so existing
-- inserts don't fail until services are redeployed.
--
-- Idempotent. Safe to re-run.
--
-- Backfill is in `phase5_company_id_backfill.sql`.
-- NOT NULL flip is in `phase5_company_id_not_null_flip.sql` (deployed
-- separately after backfill verifies clean).
--
-- The 23 tables (per Q5.1-Q5.3 sign-off):
--   Project (the bridge — exception to "reference entities stay global"
--            because line item backfill derives CompanyId from it)
--   Financial parents:    Bill, BillCredit, Expense, Invoice,
--                         ContractLabor, TimeEntry
--   Line items:           BillLineItem, BillCreditLineItem,
--                         ExpenseLineItem, InvoiceLineItem,
--                         ContractLaborLineItem, TimeLog
--   Attachments:          Attachment, BillLineItemAttachment,
--                         ExpenseLineItemAttachment,
--                         InvoiceLineItemAttachment
--   Email pipeline:       EmailMessage, EmailAttachment
--   Review:               ReviewStatus, ReviewEntry
--   Bill folder:          BillFolderRun, BillFolderRunItem

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- Helper macro pattern repeated per table. Inlined for clarity rather
-- than a dynamic SQL loop — single place to read the additions.

-- =============== Project (the bridge) ===============
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Project'))
    ALTER TABLE [dbo].[Project] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Project_Company')
    ALTER TABLE [dbo].[Project] ADD CONSTRAINT [FK_Project_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Project_CompanyId' AND object_id=OBJECT_ID('dbo.Project'))
    CREATE INDEX [IX_Project_CompanyId] ON [dbo].[Project] ([CompanyId]);
GO

-- =============== Financial parents ===============
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Bill'))
    ALTER TABLE [dbo].[Bill] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Bill_Company')
    ALTER TABLE [dbo].[Bill] ADD CONSTRAINT [FK_Bill_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Bill_CompanyId' AND object_id=OBJECT_ID('dbo.Bill'))
    CREATE INDEX [IX_Bill_CompanyId] ON [dbo].[Bill] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillCredit'))
    ALTER TABLE [dbo].[BillCredit] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillCredit_Company')
    ALTER TABLE [dbo].[BillCredit] ADD CONSTRAINT [FK_BillCredit_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCredit_CompanyId' AND object_id=OBJECT_ID('dbo.BillCredit'))
    CREATE INDEX [IX_BillCredit_CompanyId] ON [dbo].[BillCredit] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Expense'))
    ALTER TABLE [dbo].[Expense] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Expense_Company')
    ALTER TABLE [dbo].[Expense] ADD CONSTRAINT [FK_Expense_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Expense_CompanyId' AND object_id=OBJECT_ID('dbo.Expense'))
    CREATE INDEX [IX_Expense_CompanyId] ON [dbo].[Expense] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Invoice'))
    ALTER TABLE [dbo].[Invoice] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Invoice_Company')
    ALTER TABLE [dbo].[Invoice] ADD CONSTRAINT [FK_Invoice_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Invoice_CompanyId' AND object_id=OBJECT_ID('dbo.Invoice'))
    CREATE INDEX [IX_Invoice_CompanyId] ON [dbo].[Invoice] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ContractLabor'))
    ALTER TABLE [dbo].[ContractLabor] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ContractLabor_Company')
    ALTER TABLE [dbo].[ContractLabor] ADD CONSTRAINT [FK_ContractLabor_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLabor_CompanyId' AND object_id=OBJECT_ID('dbo.ContractLabor'))
    CREATE INDEX [IX_ContractLabor_CompanyId] ON [dbo].[ContractLabor] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.TimeEntry'))
    ALTER TABLE [dbo].[TimeEntry] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_TimeEntry_Company')
    ALTER TABLE [dbo].[TimeEntry] ADD CONSTRAINT [FK_TimeEntry_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeEntry_CompanyId' AND object_id=OBJECT_ID('dbo.TimeEntry'))
    CREATE INDEX [IX_TimeEntry_CompanyId] ON [dbo].[TimeEntry] ([CompanyId]);
GO

-- =============== Line items ===============
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItem'))
    ALTER TABLE [dbo].[BillLineItem] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillLineItem_Company')
    ALTER TABLE [dbo].[BillLineItem] ADD CONSTRAINT [FK_BillLineItem_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItem'))
    CREATE INDEX [IX_BillLineItem_CompanyId] ON [dbo].[BillLineItem] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem'))
    ALTER TABLE [dbo].[BillCreditLineItem] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillCreditLineItem_Company')
    ALTER TABLE [dbo].[BillCreditLineItem] ADD CONSTRAINT [FK_BillCreditLineItem_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCreditLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem'))
    CREATE INDEX [IX_BillCreditLineItem_CompanyId] ON [dbo].[BillCreditLineItem] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem'))
    ALTER TABLE [dbo].[ExpenseLineItem] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ExpenseLineItem_Company')
    ALTER TABLE [dbo].[ExpenseLineItem] ADD CONSTRAINT [FK_ExpenseLineItem_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem'))
    CREATE INDEX [IX_ExpenseLineItem_CompanyId] ON [dbo].[ExpenseLineItem] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem'))
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_InvoiceLineItem_Company')
    ALTER TABLE [dbo].[InvoiceLineItem] ADD CONSTRAINT [FK_InvoiceLineItem_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem'))
    CREATE INDEX [IX_InvoiceLineItem_CompanyId] ON [dbo].[InvoiceLineItem] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem'))
    ALTER TABLE [dbo].[ContractLaborLineItem] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ContractLaborLineItem_Company')
    ALTER TABLE [dbo].[ContractLaborLineItem] ADD CONSTRAINT [FK_ContractLaborLineItem_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLaborLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem'))
    CREATE INDEX [IX_ContractLaborLineItem_CompanyId] ON [dbo].[ContractLaborLineItem] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.TimeLog'))
    ALTER TABLE [dbo].[TimeLog] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_TimeLog_Company')
    ALTER TABLE [dbo].[TimeLog] ADD CONSTRAINT [FK_TimeLog_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeLog_CompanyId' AND object_id=OBJECT_ID('dbo.TimeLog'))
    CREATE INDEX [IX_TimeLog_CompanyId] ON [dbo].[TimeLog] ([CompanyId]);
GO

-- =============== Attachments ===============
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Attachment'))
    ALTER TABLE [dbo].[Attachment] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Attachment_Company')
    ALTER TABLE [dbo].[Attachment] ADD CONSTRAINT [FK_Attachment_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Attachment_CompanyId' AND object_id=OBJECT_ID('dbo.Attachment'))
    CREATE INDEX [IX_Attachment_CompanyId] ON [dbo].[Attachment] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment'))
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillLineItemAttachment_Company')
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [FK_BillLineItemAttachment_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItemAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment'))
    CREATE INDEX [IX_BillLineItemAttachment_CompanyId] ON [dbo].[BillLineItemAttachment] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment'))
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ExpenseLineItemAttachment_Company')
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD CONSTRAINT [FK_ExpenseLineItemAttachment_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItemAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment'))
    CREATE INDEX [IX_ExpenseLineItemAttachment_CompanyId] ON [dbo].[ExpenseLineItemAttachment] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment'))
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_InvoiceLineItemAttachment_Company')
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD CONSTRAINT [FK_InvoiceLineItemAttachment_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItemAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment'))
    CREATE INDEX [IX_InvoiceLineItemAttachment_CompanyId] ON [dbo].[InvoiceLineItemAttachment] ([CompanyId]);
GO

-- =============== Email pipeline ===============
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.EmailMessage'))
    ALTER TABLE [dbo].[EmailMessage] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_EmailMessage_Company')
    ALTER TABLE [dbo].[EmailMessage] ADD CONSTRAINT [FK_EmailMessage_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailMessage_CompanyId' AND object_id=OBJECT_ID('dbo.EmailMessage'))
    CREATE INDEX [IX_EmailMessage_CompanyId] ON [dbo].[EmailMessage] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.EmailAttachment'))
    ALTER TABLE [dbo].[EmailAttachment] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_EmailAttachment_Company')
    ALTER TABLE [dbo].[EmailAttachment] ADD CONSTRAINT [FK_EmailAttachment_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.EmailAttachment'))
    CREATE INDEX [IX_EmailAttachment_CompanyId] ON [dbo].[EmailAttachment] ([CompanyId]);
GO

-- =============== Review ===============
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ReviewStatus'))
    ALTER TABLE [dbo].[ReviewStatus] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ReviewStatus_Company')
    ALTER TABLE [dbo].[ReviewStatus] ADD CONSTRAINT [FK_ReviewStatus_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewStatus_CompanyId' AND object_id=OBJECT_ID('dbo.ReviewStatus'))
    CREATE INDEX [IX_ReviewStatus_CompanyId] ON [dbo].[ReviewStatus] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ReviewEntry'))
    ALTER TABLE [dbo].[ReviewEntry] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ReviewEntry_Company')
    ALTER TABLE [dbo].[ReviewEntry] ADD CONSTRAINT [FK_ReviewEntry_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewEntry_CompanyId' AND object_id=OBJECT_ID('dbo.ReviewEntry'))
    CREATE INDEX [IX_ReviewEntry_CompanyId] ON [dbo].[ReviewEntry] ([CompanyId]);
GO

-- =============== Bill folder ===============
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRun'))
    ALTER TABLE [dbo].[BillFolderRun] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillFolderRun_Company')
    ALTER TABLE [dbo].[BillFolderRun] ADD CONSTRAINT [FK_BillFolderRun_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRun_CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRun'))
    CREATE INDEX [IX_BillFolderRun_CompanyId] ON [dbo].[BillFolderRun] ([CompanyId]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem'))
    ALTER TABLE [dbo].[BillFolderRunItem] ADD [CompanyId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillFolderRunItem_Company')
    ALTER TABLE [dbo].[BillFolderRunItem] ADD CONSTRAINT [FK_BillFolderRunItem_Company]
        FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRunItem_CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem'))
    CREATE INDEX [IX_BillFolderRunItem_CompanyId] ON [dbo].[BillFolderRunItem] ([CompanyId]);
GO

PRINT 'Phase 5 schema migration complete — 23 tables tagged with CompanyId NULL.';
