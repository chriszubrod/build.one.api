-- Phase 5 — Multi-tenant scoping. NOT NULL flip on CompanyId across
-- 24 Phase 5 tables.
--
-- Run after:
--   1. phase5_company_id_columns.sql (schema)
--   2. phase5_company_id_backfill.sql (zero NULLs)
--   3. phase5b_review_table_fixup.sql (Review fixup)
--   4. phase5_company_id_defaults.sql (DEFAULT 1 — so new inserts populate)
--
-- Each ALTER COLUMN drops + recreates the dependent CompanyId index
-- so the column type can change. Phase 1 pattern.
--
-- Idempotent: if a column is already NOT NULL, the ALTER is a no-op
-- (with a duplicate-index check).

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- One pre-flight assertion: if any CompanyId is still NULL anywhere,
-- abort with a clear error. The flip would crash mid-table otherwise.
DECLARE @null_count INT = 0;
DECLARE @null_table NVARCHAR(200) = NULL;

SELECT @null_count = COUNT(*) FROM dbo.[Project] WHERE [CompanyId] IS NULL;       IF @null_count > 0 SET @null_table = 'Project';
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[Bill] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'Bill'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[BillCredit] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'BillCredit'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[Expense] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'Expense'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[Invoice] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'Invoice'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[ContractLabor] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'ContractLabor'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[TimeEntry] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'TimeEntry'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[BillLineItem] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'BillLineItem'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[BillCreditLineItem] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'BillCreditLineItem'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[ExpenseLineItem] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'ExpenseLineItem'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[InvoiceLineItem] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'InvoiceLineItem'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[ContractLaborLineItem] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'ContractLaborLineItem'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[TimeLog] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'TimeLog'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[Attachment] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'Attachment'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[BillLineItemAttachment] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'BillLineItemAttachment'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[ExpenseLineItemAttachment] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'ExpenseLineItemAttachment'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[InvoiceLineItemAttachment] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'InvoiceLineItemAttachment'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[EmailMessage] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'EmailMessage'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[EmailAttachment] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'EmailAttachment'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[Review] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'Review'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[ReviewStatus] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'ReviewStatus'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[ReviewEntry] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'ReviewEntry'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[BillFolderRun] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'BillFolderRun'; END
IF @null_table IS NULL BEGIN SELECT @null_count = COUNT(*) FROM dbo.[BillFolderRunItem] WHERE [CompanyId] IS NULL; IF @null_count > 0 SET @null_table = 'BillFolderRunItem'; END

IF @null_table IS NOT NULL
BEGIN
    DECLARE @msg NVARCHAR(500) = CONCAT('Phase 5 NOT NULL flip aborted: ', @null_count, ' NULL CompanyId rows in dbo.', @null_table, '. Run backfill first.');
    RAISERROR(@msg, 16, 1);
    RETURN;
END

PRINT 'Pre-flight: zero NULL CompanyIds across all 24 tables. Proceeding with NOT NULL flip.';
GO

-- Each table: drop dependent CompanyId index, ALTER COLUMN to NOT NULL,
-- recreate index. Phase 1 pattern. Idempotent via NULL-allowed check.

-- =============== Project ===============
IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Project') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Project_CompanyId' AND object_id=OBJECT_ID('dbo.Project'))
        DROP INDEX [IX_Project_CompanyId] ON [dbo].[Project];
    ALTER TABLE [dbo].[Project] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_Project_CompanyId] ON [dbo].[Project] ([CompanyId]);
    PRINT 'Project.CompanyId NOT NULL.';
END
GO

-- =============== Financial parents ===============
IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Bill') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Bill_CompanyId' AND object_id=OBJECT_ID('dbo.Bill'))
        DROP INDEX [IX_Bill_CompanyId] ON [dbo].[Bill];
    ALTER TABLE [dbo].[Bill] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_Bill_CompanyId] ON [dbo].[Bill] ([CompanyId]);
    PRINT 'Bill.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillCredit') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCredit_CompanyId' AND object_id=OBJECT_ID('dbo.BillCredit'))
        DROP INDEX [IX_BillCredit_CompanyId] ON [dbo].[BillCredit];
    ALTER TABLE [dbo].[BillCredit] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillCredit_CompanyId] ON [dbo].[BillCredit] ([CompanyId]);
    PRINT 'BillCredit.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Expense') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Expense_CompanyId' AND object_id=OBJECT_ID('dbo.Expense'))
        DROP INDEX [IX_Expense_CompanyId] ON [dbo].[Expense];
    ALTER TABLE [dbo].[Expense] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_Expense_CompanyId] ON [dbo].[Expense] ([CompanyId]);
    PRINT 'Expense.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Invoice') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Invoice_CompanyId' AND object_id=OBJECT_ID('dbo.Invoice'))
        DROP INDEX [IX_Invoice_CompanyId] ON [dbo].[Invoice];
    ALTER TABLE [dbo].[Invoice] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_Invoice_CompanyId] ON [dbo].[Invoice] ([CompanyId]);
    PRINT 'Invoice.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ContractLabor') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLabor_CompanyId' AND object_id=OBJECT_ID('dbo.ContractLabor'))
        DROP INDEX [IX_ContractLabor_CompanyId] ON [dbo].[ContractLabor];
    ALTER TABLE [dbo].[ContractLabor] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_ContractLabor_CompanyId] ON [dbo].[ContractLabor] ([CompanyId]);
    PRINT 'ContractLabor.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.TimeEntry') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeEntry_CompanyId' AND object_id=OBJECT_ID('dbo.TimeEntry'))
        DROP INDEX [IX_TimeEntry_CompanyId] ON [dbo].[TimeEntry];
    ALTER TABLE [dbo].[TimeEntry] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_TimeEntry_CompanyId] ON [dbo].[TimeEntry] ([CompanyId]);
    PRINT 'TimeEntry.CompanyId NOT NULL.';
END
GO

-- =============== Line items ===============
IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItem'))
        DROP INDEX [IX_BillLineItem_CompanyId] ON [dbo].[BillLineItem];
    ALTER TABLE [dbo].[BillLineItem] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillLineItem_CompanyId] ON [dbo].[BillLineItem] ([CompanyId]);
    PRINT 'BillLineItem.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillCreditLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.BillCreditLineItem'))
        DROP INDEX [IX_BillCreditLineItem_CompanyId] ON [dbo].[BillCreditLineItem];
    ALTER TABLE [dbo].[BillCreditLineItem] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillCreditLineItem_CompanyId] ON [dbo].[BillCreditLineItem] ([CompanyId]);
    PRINT 'BillCreditLineItem.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItem'))
        DROP INDEX [IX_ExpenseLineItem_CompanyId] ON [dbo].[ExpenseLineItem];
    ALTER TABLE [dbo].[ExpenseLineItem] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_ExpenseLineItem_CompanyId] ON [dbo].[ExpenseLineItem] ([CompanyId]);
    PRINT 'ExpenseLineItem.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItem'))
        DROP INDEX [IX_InvoiceLineItem_CompanyId] ON [dbo].[InvoiceLineItem];
    ALTER TABLE [dbo].[InvoiceLineItem] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_InvoiceLineItem_CompanyId] ON [dbo].[InvoiceLineItem] ([CompanyId]);
    PRINT 'InvoiceLineItem.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ContractLaborLineItem_CompanyId' AND object_id=OBJECT_ID('dbo.ContractLaborLineItem'))
        DROP INDEX [IX_ContractLaborLineItem_CompanyId] ON [dbo].[ContractLaborLineItem];
    ALTER TABLE [dbo].[ContractLaborLineItem] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_ContractLaborLineItem_CompanyId] ON [dbo].[ContractLaborLineItem] ([CompanyId]);
    PRINT 'ContractLaborLineItem.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.TimeLog') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_TimeLog_CompanyId' AND object_id=OBJECT_ID('dbo.TimeLog'))
        DROP INDEX [IX_TimeLog_CompanyId] ON [dbo].[TimeLog];
    ALTER TABLE [dbo].[TimeLog] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_TimeLog_CompanyId] ON [dbo].[TimeLog] ([CompanyId]);
    PRINT 'TimeLog.CompanyId NOT NULL.';
END
GO

-- =============== Attachments ===============
IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Attachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Attachment_CompanyId' AND object_id=OBJECT_ID('dbo.Attachment'))
        DROP INDEX [IX_Attachment_CompanyId] ON [dbo].[Attachment];
    ALTER TABLE [dbo].[Attachment] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_Attachment_CompanyId] ON [dbo].[Attachment] ([CompanyId]);
    PRINT 'Attachment.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillLineItemAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.BillLineItemAttachment'))
        DROP INDEX [IX_BillLineItemAttachment_CompanyId] ON [dbo].[BillLineItemAttachment];
    ALTER TABLE [dbo].[BillLineItemAttachment] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillLineItemAttachment_CompanyId] ON [dbo].[BillLineItemAttachment] ([CompanyId]);
    PRINT 'BillLineItemAttachment.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ExpenseLineItemAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.ExpenseLineItemAttachment'))
        DROP INDEX [IX_ExpenseLineItemAttachment_CompanyId] ON [dbo].[ExpenseLineItemAttachment];
    ALTER TABLE [dbo].[ExpenseLineItemAttachment] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_ExpenseLineItemAttachment_CompanyId] ON [dbo].[ExpenseLineItemAttachment] ([CompanyId]);
    PRINT 'ExpenseLineItemAttachment.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_InvoiceLineItemAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.InvoiceLineItemAttachment'))
        DROP INDEX [IX_InvoiceLineItemAttachment_CompanyId] ON [dbo].[InvoiceLineItemAttachment];
    ALTER TABLE [dbo].[InvoiceLineItemAttachment] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_InvoiceLineItemAttachment_CompanyId] ON [dbo].[InvoiceLineItemAttachment] ([CompanyId]);
    PRINT 'InvoiceLineItemAttachment.CompanyId NOT NULL.';
END
GO

-- =============== Email pipeline ===============
IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.EmailMessage') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailMessage_CompanyId' AND object_id=OBJECT_ID('dbo.EmailMessage'))
        DROP INDEX [IX_EmailMessage_CompanyId] ON [dbo].[EmailMessage];
    ALTER TABLE [dbo].[EmailMessage] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_EmailMessage_CompanyId] ON [dbo].[EmailMessage] ([CompanyId]);
    PRINT 'EmailMessage.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.EmailAttachment') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_EmailAttachment_CompanyId' AND object_id=OBJECT_ID('dbo.EmailAttachment'))
        DROP INDEX [IX_EmailAttachment_CompanyId] ON [dbo].[EmailAttachment];
    ALTER TABLE [dbo].[EmailAttachment] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_EmailAttachment_CompanyId] ON [dbo].[EmailAttachment] ([CompanyId]);
    PRINT 'EmailAttachment.CompanyId NOT NULL.';
END
GO

-- =============== Review (live) + ReviewStatus + ReviewEntry ===============
IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.Review') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Review_CompanyId' AND object_id=OBJECT_ID('dbo.Review'))
        DROP INDEX [IX_Review_CompanyId] ON [dbo].[Review];
    ALTER TABLE [dbo].[Review] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_Review_CompanyId] ON [dbo].[Review] ([CompanyId]);
    PRINT 'Review.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ReviewStatus') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewStatus_CompanyId' AND object_id=OBJECT_ID('dbo.ReviewStatus'))
        DROP INDEX [IX_ReviewStatus_CompanyId] ON [dbo].[ReviewStatus];
    ALTER TABLE [dbo].[ReviewStatus] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_ReviewStatus_CompanyId] ON [dbo].[ReviewStatus] ([CompanyId]);
    PRINT 'ReviewStatus.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.ReviewEntry') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_ReviewEntry_CompanyId' AND object_id=OBJECT_ID('dbo.ReviewEntry'))
        DROP INDEX [IX_ReviewEntry_CompanyId] ON [dbo].[ReviewEntry];
    ALTER TABLE [dbo].[ReviewEntry] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_ReviewEntry_CompanyId] ON [dbo].[ReviewEntry] ([CompanyId]);
    PRINT 'ReviewEntry.CompanyId NOT NULL.';
END
GO

-- =============== Bill folder ===============
IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRun') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRun_CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRun'))
        DROP INDEX [IX_BillFolderRun_CompanyId] ON [dbo].[BillFolderRun];
    ALTER TABLE [dbo].[BillFolderRun] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillFolderRun_CompanyId] ON [dbo].[BillFolderRun] ([CompanyId]);
    PRINT 'BillFolderRun.CompanyId NOT NULL.';
END
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE name='CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem') AND is_nullable=1)
BEGIN
    IF EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_BillFolderRunItem_CompanyId' AND object_id=OBJECT_ID('dbo.BillFolderRunItem'))
        DROP INDEX [IX_BillFolderRunItem_CompanyId] ON [dbo].[BillFolderRunItem];
    ALTER TABLE [dbo].[BillFolderRunItem] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
    CREATE INDEX [IX_BillFolderRunItem_CompanyId] ON [dbo].[BillFolderRunItem] ([CompanyId]);
    PRINT 'BillFolderRunItem.CompanyId NOT NULL.';
END
GO

PRINT 'Phase 5 NOT NULL flip complete — 24 tables.';
