-- =========================================================================
-- 238c_qbo_identity_reference.sql
--
-- U-238c: Add dbo-native QboId/RealmId columns to eight reference entities,
-- sourced from existing qbo.* mapping + staging tables. Purely additive —
-- qbo.* tables and mapping rows are untouched. No SyncToken (pull-only / no
-- live push path on any of these eight).
--
-- Pre-flight duplicate awareness (all QboId NULL today — cannot fire on first
-- run, but backfill must re-check before stamping):
--   SELECT [QboId], [RealmId], COUNT(*) FROM dbo.[Vendor] WHERE [QboId] IS NOT NULL
--     GROUP BY [QboId], [RealmId] HAVING COUNT(*) > 1;
--   (repeat for Customer, CostCode, SubCostCode, PaymentTerm, Address,
--    Attachment, BillCredit)
--
-- Run: python scripts/run_sql.py scripts/migrations/238c_qbo_identity_reference.sql
-- =========================================================================

-- dbo.Vendor
IF OBJECT_ID('dbo.Vendor', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Vendor') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Vendor] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Vendor', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Vendor') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Vendor] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Vendor', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Vendor_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Vendor')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Vendor_QboId_RealmId ON [dbo].[Vendor] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.Customer
IF OBJECT_ID('dbo.Customer', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Customer') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Customer] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Customer', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Customer') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Customer] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Customer_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Customer')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Customer_QboId_RealmId ON [dbo].[Customer] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.CostCode
IF OBJECT_ID('dbo.CostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.CostCode') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[CostCode] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.CostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.CostCode') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[CostCode] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.CostCode', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_CostCode_QboId_RealmId' AND object_id = OBJECT_ID('dbo.CostCode')
)
BEGIN
    CREATE UNIQUE INDEX UQ_CostCode_QboId_RealmId ON [dbo].[CostCode] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.SubCostCode
IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.SubCostCode') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.SubCostCode') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_SubCostCode_QboId_RealmId' AND object_id = OBJECT_ID('dbo.SubCostCode')
)
BEGIN
    CREATE UNIQUE INDEX UQ_SubCostCode_QboId_RealmId ON [dbo].[SubCostCode] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.PaymentTerm
IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.PaymentTerm') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[PaymentTerm] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.PaymentTerm') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[PaymentTerm] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_PaymentTerm_QboId_RealmId' AND object_id = OBJECT_ID('dbo.PaymentTerm')
)
BEGIN
    CREATE UNIQUE INDEX UQ_PaymentTerm_QboId_RealmId ON [dbo].[PaymentTerm] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.Address
IF OBJECT_ID('dbo.Address', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Address') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Address] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Address', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Address') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Address] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Address', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Address_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Address')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Address_QboId_RealmId ON [dbo].[Address] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.Attachment
IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Attachment') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[Attachment] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Attachment') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[Attachment] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_Attachment_QboId_RealmId' AND object_id = OBJECT_ID('dbo.Attachment')
)
BEGIN
    CREATE UNIQUE INDEX UQ_Attachment_QboId_RealmId ON [dbo].[Attachment] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- dbo.BillCredit
IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillCredit') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[BillCredit] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillCredit') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[BillCredit] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_BillCredit_QboId_RealmId' AND object_id = OBJECT_ID('dbo.BillCredit')
)
BEGIN
    CREATE UNIQUE INDEX UQ_BillCredit_QboId_RealmId ON [dbo].[BillCredit] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO
