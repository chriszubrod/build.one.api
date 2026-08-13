-- =========================================================================
-- 238a_qbo_identity_headers.sql
--
-- U-238a: Add dbo-native QboId/RealmId/(SyncToken) columns to five header
-- entities, sourced from existing qbo.* mapping + staging tables.
-- Purely additive — qbo.* tables and mapping rows are untouched.
--
-- Run: python scripts/run_sql.py scripts/migrations/238a_qbo_identity_headers.sql
-- =========================================================================

-- dbo.Bill
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'SyncToken')
BEGIN
    ALTER TABLE [dbo].[Bill] ADD [SyncToken] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Bill_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Bill')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Bill_QboId_RealmId ON [dbo].[Bill] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.Expense
IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'SyncToken')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [SyncToken] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Expense_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Expense')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Expense_QboId_RealmId ON [dbo].[Expense] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.Invoice
IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Invoice') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Invoice] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Invoice') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Invoice] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Invoice') AND name = 'SyncToken')
BEGIN
    ALTER TABLE [dbo].[Invoice] ADD [SyncToken] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Invoice_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Invoice')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Invoice_QboId_RealmId ON [dbo].[Invoice] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.Project (pull-only — no SyncToken)
IF OBJECT_ID('dbo.Project', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Project') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Project] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Project', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Project') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Project] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Project', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Project_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Project')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Project_QboId_RealmId ON [dbo].[Project] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.Company (pull-only — no SyncToken)
IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Company') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Company] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Company') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Company] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Company', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Company_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Company')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Company_QboId_RealmId ON [dbo].[Company] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO
