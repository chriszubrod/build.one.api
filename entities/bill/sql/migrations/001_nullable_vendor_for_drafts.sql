-- Migration: Allow draft bills without a vendor or bill number
-- VendorId and BillNumber become nullable so inbox drafts can be saved
-- before the user has confirmed vendor/invoice details.
-- Replaces unique constraint with non-unique composite index for lookups.

-- 1. Drop the existing unique constraint (if it was a table constraint)
IF EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_Bill_VendorId_BillNumber' AND parent_object_id = OBJECT_ID('dbo.Bill'))
BEGIN
    ALTER TABLE [dbo].[Bill] DROP CONSTRAINT [UQ_Bill_VendorId_BillNumber];
END
GO

-- 1b. Drop if it was already recreated as an index (re-run safe)
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Bill_VendorId_BillNumber' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
    DROP INDEX [UQ_Bill_VendorId_BillNumber] ON [dbo].[Bill];
END
GO

-- 2. Make VendorId nullable
ALTER TABLE [dbo].[Bill] ALTER COLUMN [VendorId] BIGINT NULL;
GO

-- 3. Make BillNumber nullable
ALTER TABLE [dbo].[Bill] ALTER COLUMN [BillNumber] NVARCHAR(50) NULL;
GO

-- 4. Create non-unique composite index for duplicate-check lookups
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_VendorId_BillDate_BillNumber' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
    CREATE INDEX [IX_Bill_VendorId_BillDate_BillNumber]
        ON [dbo].[Bill] ([VendorId], [BillDate], [BillNumber]);
END
GO
