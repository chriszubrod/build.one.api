-- Phase 5 — Multi-tenant scoping. DEFAULT constraints on CompanyId.
--
-- Adds DEFAULT (1) constraint to each newly-added CompanyId column on
-- the 24 Phase 5 tables. New inserts that don't specify CompanyId pick
-- up the default automatically — no sproc changes needed for Phase 5.
--
-- Default value is hardcoded to 1, the Id of "Rogers Build, Inc." (the
-- single existing Company today). The day a second Company arrives, the
-- multi-tenant cutover work (Phase 5b) will:
--   1. Update Create sprocs to accept @CompanyId BIGINT param.
--   2. Update services to pass CompanyId from current_company_id ContextVar.
--   3. Drop these DEFAULT constraints so explicit values are required.
--
-- Idempotent. Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

DECLARE @DefaultCompanyId BIGINT = 1;

-- Inline check + add. Each constraint name is unique; SQL Server's
-- IF NOT EXISTS guard on sys.default_constraints prevents duplication.

-- =============== Project ===============
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Project_CompanyId')
    ALTER TABLE [dbo].[Project] ADD CONSTRAINT [DF_Project_CompanyId] DEFAULT (1) FOR [CompanyId];
GO

-- =============== Financial parents ===============
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Bill_CompanyId')
    ALTER TABLE [dbo].[Bill] ADD CONSTRAINT [DF_Bill_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillCredit_CompanyId')
    ALTER TABLE [dbo].[BillCredit] ADD CONSTRAINT [DF_BillCredit_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Expense_CompanyId')
    ALTER TABLE [dbo].[Expense] ADD CONSTRAINT [DF_Expense_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Invoice_CompanyId')
    ALTER TABLE [dbo].[Invoice] ADD CONSTRAINT [DF_Invoice_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ContractLabor_CompanyId')
    ALTER TABLE [dbo].[ContractLabor] ADD CONSTRAINT [DF_ContractLabor_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_TimeEntry_CompanyId')
    ALTER TABLE [dbo].[TimeEntry] ADD CONSTRAINT [DF_TimeEntry_CompanyId] DEFAULT (1) FOR [CompanyId];
GO

-- =============== Line items ===============
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillLineItem_CompanyId')
    ALTER TABLE [dbo].[BillLineItem] ADD CONSTRAINT [DF_BillLineItem_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillCreditLineItem_CompanyId')
    ALTER TABLE [dbo].[BillCreditLineItem] ADD CONSTRAINT [DF_BillCreditLineItem_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ExpenseLineItem_CompanyId')
    ALTER TABLE [dbo].[ExpenseLineItem] ADD CONSTRAINT [DF_ExpenseLineItem_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_InvoiceLineItem_CompanyId')
    ALTER TABLE [dbo].[InvoiceLineItem] ADD CONSTRAINT [DF_InvoiceLineItem_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ContractLaborLineItem_CompanyId')
    ALTER TABLE [dbo].[ContractLaborLineItem] ADD CONSTRAINT [DF_ContractLaborLineItem_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_TimeLog_CompanyId')
    ALTER TABLE [dbo].[TimeLog] ADD CONSTRAINT [DF_TimeLog_CompanyId] DEFAULT (1) FOR [CompanyId];
GO

-- =============== Attachments ===============
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Attachment_CompanyId')
    ALTER TABLE [dbo].[Attachment] ADD CONSTRAINT [DF_Attachment_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillLineItemAttachment_CompanyId')
    ALTER TABLE [dbo].[BillLineItemAttachment] ADD CONSTRAINT [DF_BillLineItemAttachment_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ExpenseLineItemAttachment_CompanyId')
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ADD CONSTRAINT [DF_ExpenseLineItemAttachment_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_InvoiceLineItemAttachment_CompanyId')
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ADD CONSTRAINT [DF_InvoiceLineItemAttachment_CompanyId] DEFAULT (1) FOR [CompanyId];
GO

-- =============== Email pipeline ===============
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_EmailMessage_CompanyId')
    ALTER TABLE [dbo].[EmailMessage] ADD CONSTRAINT [DF_EmailMessage_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_EmailAttachment_CompanyId')
    ALTER TABLE [dbo].[EmailAttachment] ADD CONSTRAINT [DF_EmailAttachment_CompanyId] DEFAULT (1) FOR [CompanyId];
GO

-- =============== Review (live table) + ReviewStatus + dead-schema ReviewEntry ===============
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_Review_CompanyId')
    ALTER TABLE [dbo].[Review] ADD CONSTRAINT [DF_Review_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ReviewStatus_CompanyId')
    ALTER TABLE [dbo].[ReviewStatus] ADD CONSTRAINT [DF_ReviewStatus_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_ReviewEntry_CompanyId')
    ALTER TABLE [dbo].[ReviewEntry] ADD CONSTRAINT [DF_ReviewEntry_CompanyId] DEFAULT (1) FOR [CompanyId];
GO

-- =============== Bill folder ===============
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillFolderRun_CompanyId')
    ALTER TABLE [dbo].[BillFolderRun] ADD CONSTRAINT [DF_BillFolderRun_CompanyId] DEFAULT (1) FOR [CompanyId];
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name='DF_BillFolderRunItem_CompanyId')
    ALTER TABLE [dbo].[BillFolderRunItem] ADD CONSTRAINT [DF_BillFolderRunItem_CompanyId] DEFAULT (1) FOR [CompanyId];
GO

PRINT 'Phase 5 DEFAULT constraints applied — 24 tables.';
