-- Gap 2 — Audit attribution on transactional + reference entities.
-- Adds CreatedByUserId BIGINT NULL FK User.Id + index to 30 tables that
-- don't already have audit attribution. Phase 1 already covered the 7
-- access-control entities (Organization, Company, User, UserRole,
-- UserModule, UserCompany, UserProject) and Phase 4 covered Workflow +
-- WorkflowEvent. This migration covers the remaining 30.
--
-- One file, dependency-ordered:
--   1. ADD COLUMN nullable + FK + index (idempotent CREATE OR ALTER pattern)
--   2. Backfill all rows to Christopher (User.Id=17) — per Q2.3 sign-off
--   3. DEFAULT (17) constraints — same Phase 5 trick: existing INSERT
--      statements omit CreatedByUserId, SQL Server applies DEFAULT
--   4. NOT NULL flip with pre-flight assertion
--
-- ModifiedByUserId is NOT added (Q2.2 = c). The Workflow audit trail
-- already captures every CRUD with actor + timestamp; row-level
-- "who last touched it" is reachable via Workflow.{ParentId} JOIN.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- =====================================================================
-- 1. ADD COLUMN nullable + FK + index
-- =====================================================================

-- The 24 Phase 5 tables
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Project'))
    ALTER TABLE [dbo].[Project] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Project_CreatedByUser')
    ALTER TABLE [dbo].[Project] ADD CONSTRAINT [FK_Project_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Project_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Project'))
    CREATE INDEX [IX_Project_CreatedByUserId] ON [dbo].[Project] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Bill'))
    ALTER TABLE [dbo].[Bill] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Bill_CreatedByUser')
    ALTER TABLE [dbo].[Bill] ADD CONSTRAINT [FK_Bill_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Bill_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Bill'))
    CREATE INDEX [IX_Bill_CreatedByUserId] ON [dbo].[Bill] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCredit'))
    ALTER TABLE [dbo].[BillCredit] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillCredit_CreatedByUser')
    ALTER TABLE [dbo].[BillCredit] ADD CONSTRAINT [FK_BillCredit_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCredit_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCredit'))
    CREATE INDEX [IX_BillCredit_CreatedByUserId] ON [dbo].[BillCredit] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Expense'))
    ALTER TABLE [dbo].[Expense] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Expense_CreatedByUser')
    ALTER TABLE [dbo].[Expense] ADD CONSTRAINT [FK_Expense_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Expense_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Expense'))
    CREATE INDEX [IX_Expense_CreatedByUserId] ON [dbo].[Expense] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Invoice'))
    ALTER TABLE [dbo].[Invoice] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Invoice_CreatedByUser')
    ALTER TABLE [dbo].[Invoice] ADD CONSTRAINT [FK_Invoice_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Invoice_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Invoice'))
    CREATE INDEX [IX_Invoice_CreatedByUserId] ON [dbo].[Invoice] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLabor'))
    ALTER TABLE [dbo].[ContractLabor] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ContractLabor_CreatedByUser')
    ALTER TABLE [dbo].[ContractLabor] ADD CONSTRAINT [FK_ContractLabor_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLabor_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLabor'))
    CREATE INDEX [IX_ContractLabor_CreatedByUserId] ON [dbo].[ContractLabor] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeEntry'))
    ALTER TABLE [dbo].[TimeEntry] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_TimeEntry_CreatedByUser')
    ALTER TABLE [dbo].[TimeEntry] ADD CONSTRAINT [FK_TimeEntry_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeEntry_CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeEntry'))
    CREATE INDEX [IX_TimeEntry_CreatedByUserId] ON [dbo].[TimeEntry] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItem'))
    ALTER TABLE [dbo].[BillLineItem] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillLineItem_CreatedByUser')
    ALTER TABLE [dbo].[BillLineItem] ADD CONSTRAINT [FK_BillLineItem_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItem'))
    CREATE INDEX [IX_BillLineItem_CreatedByUserId] ON [dbo].[BillLineItem] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem'))
    ALTER TABLE [dbo].[BillCreditLineItem] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillCreditLineItem_CreatedByUser')
    ALTER TABLE [dbo].[BillCreditLineItem] ADD CONSTRAINT [FK_BillCreditLineItem_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCreditLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem'))
    CREATE INDEX [IX_BillCreditLineItem_CreatedByUserId] ON [dbo].[BillCreditLineItem] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem'))
    ALTER TABLE [dbo].[ExpenseLineItem] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ExpenseLineItem_CreatedByUser')
    ALTER TABLE [dbo].[ExpenseLineItem] ADD CONSTRAINT [FK_ExpenseLineItem_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem'))
    CREATE INDEX [IX_ExpenseLineItem_CreatedByUserId] ON [dbo].[ExpenseLineItem] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem'))
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_InvoiceLineItem_CreatedByUser')
    ALTER TABLE [dbo].[InvoiceLineItem] ADD CONSTRAINT [FK_InvoiceLineItem_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem'))
    CREATE INDEX [IX_InvoiceLineItem_CreatedByUserId] ON [dbo].[InvoiceLineItem] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem'))
    ALTER TABLE [dbo].[ContractLaborLineItem] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ContractLaborLineItem_CreatedByUser')
    ALTER TABLE [dbo].[ContractLaborLineItem] ADD CONSTRAINT [FK_ContractLaborLineItem_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLaborLineItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem'))
    CREATE INDEX [IX_ContractLaborLineItem_CreatedByUserId] ON [dbo].[ContractLaborLineItem] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeLog'))
    ALTER TABLE [dbo].[TimeLog] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_TimeLog_CreatedByUser')
    ALTER TABLE [dbo].[TimeLog] ADD CONSTRAINT [FK_TimeLog_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeLog_CreatedByUserId' AND object_id=OBJECT_ID('dbo.TimeLog'))
    CREATE INDEX [IX_TimeLog_CreatedByUserId] ON [dbo].[TimeLog] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Attachment'))
    ALTER TABLE [dbo].[Attachment] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Attachment_CreatedByUser')
    ALTER TABLE [dbo].[Attachment] ADD CONSTRAINT [FK_Attachment_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Attachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Attachment'))
    CREATE INDEX [IX_Attachment_CreatedByUserId] ON [dbo].[Attachment] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment'))
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillLineItemAttachment_CreatedByUser')
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [FK_BillLineItemAttachment_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItemAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment'))
    CREATE INDEX [IX_BillLineItemAttachment_CreatedByUserId] ON [dbo].[BillLineItemAttachment] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment'))
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ExpenseLineItemAttachment_CreatedByUser')
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD CONSTRAINT [FK_ExpenseLineItemAttachment_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItemAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment'))
    CREATE INDEX [IX_ExpenseLineItemAttachment_CreatedByUserId] ON [dbo].[ExpenseLineItemAttachment] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment'))
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_InvoiceLineItemAttachment_CreatedByUser')
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD CONSTRAINT [FK_InvoiceLineItemAttachment_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItemAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment'))
    CREATE INDEX [IX_InvoiceLineItemAttachment_CreatedByUserId] ON [dbo].[InvoiceLineItemAttachment] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailMessage'))
    ALTER TABLE [dbo].[EmailMessage] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_EmailMessage_CreatedByUser')
    ALTER TABLE [dbo].[EmailMessage] ADD CONSTRAINT [FK_EmailMessage_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailMessage_CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailMessage'))
    CREATE INDEX [IX_EmailMessage_CreatedByUserId] ON [dbo].[EmailMessage] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailAttachment'))
    ALTER TABLE [dbo].[EmailAttachment] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_EmailAttachment_CreatedByUser')
    ALTER TABLE [dbo].[EmailAttachment] ADD CONSTRAINT [FK_EmailAttachment_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailAttachment_CreatedByUserId' AND object_id=OBJECT_ID('dbo.EmailAttachment'))
    CREATE INDEX [IX_EmailAttachment_CreatedByUserId] ON [dbo].[EmailAttachment] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Review'))
    ALTER TABLE [dbo].[Review] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Review_CreatedByUser')
    ALTER TABLE [dbo].[Review] ADD CONSTRAINT [FK_Review_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Review_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Review'))
    CREATE INDEX [IX_Review_CreatedByUserId] ON [dbo].[Review] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewStatus'))
    ALTER TABLE [dbo].[ReviewStatus] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ReviewStatus_CreatedByUser')
    ALTER TABLE [dbo].[ReviewStatus] ADD CONSTRAINT [FK_ReviewStatus_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewStatus_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewStatus'))
    CREATE INDEX [IX_ReviewStatus_CreatedByUserId] ON [dbo].[ReviewStatus] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewEntry'))
    ALTER TABLE [dbo].[ReviewEntry] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ReviewEntry_CreatedByUser')
    ALTER TABLE [dbo].[ReviewEntry] ADD CONSTRAINT [FK_ReviewEntry_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewEntry_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ReviewEntry'))
    CREATE INDEX [IX_ReviewEntry_CreatedByUserId] ON [dbo].[ReviewEntry] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRun'))
    ALTER TABLE [dbo].[BillFolderRun] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillFolderRun_CreatedByUser')
    ALTER TABLE [dbo].[BillFolderRun] ADD CONSTRAINT [FK_BillFolderRun_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRun_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRun'))
    CREATE INDEX [IX_BillFolderRun_CreatedByUserId] ON [dbo].[BillFolderRun] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem'))
    ALTER TABLE [dbo].[BillFolderRunItem] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_BillFolderRunItem_CreatedByUser')
    ALTER TABLE [dbo].[BillFolderRunItem] ADD CONSTRAINT [FK_BillFolderRunItem_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRunItem_CreatedByUserId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem'))
    CREATE INDEX [IX_BillFolderRunItem_CreatedByUserId] ON [dbo].[BillFolderRunItem] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

-- Reference entities (per Q2.1=b)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Vendor'))
    ALTER TABLE [dbo].[Vendor] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Vendor_CreatedByUser')
    ALTER TABLE [dbo].[Vendor] ADD CONSTRAINT [FK_Vendor_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Vendor_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Vendor'))
    CREATE INDEX [IX_Vendor_CreatedByUserId] ON [dbo].[Vendor] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.Customer'))
    ALTER TABLE [dbo].[Customer] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Customer_CreatedByUser')
    ALTER TABLE [dbo].[Customer] ADD CONSTRAINT [FK_Customer_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Customer_CreatedByUserId' AND object_id=OBJECT_ID('dbo.Customer'))
    CREATE INDEX [IX_Customer_CreatedByUserId] ON [dbo].[Customer] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.SubCostCode'))
    ALTER TABLE [dbo].[SubCostCode] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_SubCostCode_CreatedByUser')
    ALTER TABLE [dbo].[SubCostCode] ADD CONSTRAINT [FK_SubCostCode_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_SubCostCode_CreatedByUserId' AND object_id=OBJECT_ID('dbo.SubCostCode'))
    CREATE INDEX [IX_SubCostCode_CreatedByUserId] ON [dbo].[SubCostCode] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.CostCode'))
    ALTER TABLE [dbo].[CostCode] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_CostCode_CreatedByUser')
    ALTER TABLE [dbo].[CostCode] ADD CONSTRAINT [FK_CostCode_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_CostCode_CreatedByUserId' AND object_id=OBJECT_ID('dbo.CostCode'))
    CREATE INDEX [IX_CostCode_CreatedByUserId] ON [dbo].[CostCode] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.PaymentTerm'))
    ALTER TABLE [dbo].[PaymentTerm] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_PaymentTerm_CreatedByUser')
    ALTER TABLE [dbo].[PaymentTerm] ADD CONSTRAINT [FK_PaymentTerm_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_PaymentTerm_CreatedByUserId' AND object_id=OBJECT_ID('dbo.PaymentTerm'))
    CREATE INDEX [IX_PaymentTerm_CreatedByUserId] ON [dbo].[PaymentTerm] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='CreatedByUserId' AND object_id=OBJECT_ID('dbo.ProjectAddress'))
    ALTER TABLE [dbo].[ProjectAddress] ADD [CreatedByUserId] BIGINT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_ProjectAddress_CreatedByUser')
    ALTER TABLE [dbo].[ProjectAddress] ADD CONSTRAINT [FK_ProjectAddress_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ProjectAddress_CreatedByUserId' AND object_id=OBJECT_ID('dbo.ProjectAddress'))
    CREATE INDEX [IX_ProjectAddress_CreatedByUserId] ON [dbo].[ProjectAddress] ([CreatedByUserId]) WHERE [CreatedByUserId] IS NOT NULL;
GO

PRINT 'Gap 2 schema migration complete — 30 tables tagged with CreatedByUserId NULL.';
