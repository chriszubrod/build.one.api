-- =========================================================================
-- u218d_qbo_staging_unique_indexes.sql
--
-- Filtered unique indexes on (QboId, RealmId) for the eight QBO staging
-- tables that lacked uniqueness when a pull double-ran (U-218d).
--
-- PREREQUISITE: u218d_qbo_staging_dedupe.sql must be applied and verified first.
--
-- No duplicate-check guard that skips CREATE — scripts/run_sql.py never reads
-- cursor.messages, so a skipped index would report success while the index is
-- absent. Dedupe is verified first; if data is still dirty, CREATE UNIQUE INDEX
-- must FAIL LOUDLY rather than silently no-op.
--
-- Run: python scripts/run_sql.py scripts/migrations/u218d_qbo_staging_unique_indexes.sql
-- =========================================================================

IF OBJECT_ID('qbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboBill_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.Bill')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboBill_QboId_RealmId ON [qbo].[Bill] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO

IF OBJECT_ID('qbo.Purchase', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboPurchase_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.Purchase')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboPurchase_QboId_RealmId ON [qbo].[Purchase] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO

IF OBJECT_ID('qbo.Vendor', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboVendor_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.Vendor')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboVendor_QboId_RealmId ON [qbo].[Vendor] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO

IF OBJECT_ID('qbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboCustomer_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.Customer')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboCustomer_QboId_RealmId ON [qbo].[Customer] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO

IF OBJECT_ID('qbo.Item', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboItem_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.Item')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboItem_QboId_RealmId ON [qbo].[Item] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO

IF OBJECT_ID('qbo.Account', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboAccount_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.Account')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboAccount_QboId_RealmId ON [qbo].[Account] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO

IF OBJECT_ID('qbo.Term', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboTerm_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.Term')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboTerm_QboId_RealmId ON [qbo].[Term] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO

-- Drop redundant non-unique composite before adding the unique index on the same pair.
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboReimburseCharge_QboId_RealmId' AND object_id = OBJECT_ID('qbo.ReimburseCharge'))
BEGIN
    DROP INDEX IX_QboReimburseCharge_QboId_RealmId ON [qbo].[ReimburseCharge];
END
GO

IF OBJECT_ID('qbo.ReimburseCharge', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes i
    WHERE i.name = 'UQ_QboReimburseCharge_QboId_RealmId'
      AND i.object_id = OBJECT_ID('qbo.ReimburseCharge')
      AND i.is_unique = 1
      AND i.is_disabled = 0
      AND i.ignore_dup_key = 0
      AND (
          SELECT STRING_AGG(COL_NAME(ic.object_id, ic.column_id), ',')
                 WITHIN GROUP (ORDER BY ic.key_ordinal)
          FROM sys.index_columns ic
          WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
      ) = N'QboId,RealmId'
)
BEGIN
CREATE UNIQUE INDEX UQ_QboReimburseCharge_QboId_RealmId ON [qbo].[ReimburseCharge] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
END
GO
