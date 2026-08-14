-- =========================================================================
-- 238b_qbo_identity_lines.sql
--
-- U-238b: Add dbo-native QboId/RealmId columns to four line-item entities,
-- sourced from existing qbo.* mapping + staging tables (line QboLineId +
-- parent header RealmId). Purely additive — qbo.* tables untouched.
-- Line-level uniqueness is scoped per parent (QBO line ids collide across
-- different parent transactions within a realm).
--
-- Run: python scripts/run_sql.py scripts/migrations/238b_qbo_identity_lines.sql
-- =========================================================================

-- dbo.BillLineItem
IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_BillLineItem_BillId_QboId' AND object_id = OBJECT_ID('dbo.BillLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_BillLineItem_BillId_QboId ON [dbo].[BillLineItem] ([BillId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.InvoiceLineItem
IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.InvoiceLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.InvoiceLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_InvoiceLineItem_InvoiceId_QboId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_InvoiceLineItem_InvoiceId_QboId ON [dbo].[InvoiceLineItem] ([InvoiceId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.ExpenseLineItem
IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.ExpenseLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.ExpenseLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_ExpenseLineItem_ExpenseId_QboId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_ExpenseLineItem_ExpenseId_QboId ON [dbo].[ExpenseLineItem] ([ExpenseId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.BillCreditLineItem
IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillCreditLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[BillCreditLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillCreditLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[BillCreditLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_BillCreditLineItem_BillCreditId_QboId' AND object_id = OBJECT_ID('dbo.BillCreditLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_BillCreditLineItem_BillCreditId_QboId ON [dbo].[BillCreditLineItem] ([BillCreditId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO
