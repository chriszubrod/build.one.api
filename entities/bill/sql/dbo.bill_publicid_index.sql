-- Contract: dbo.Bill   (U-699; index-only migration slice, mirrored in dbo.bill.sql)
-- Owns: UQ_Bill_PublicId — unique seek index for ReadBillByPublicId and every read_by_public_id.
-- Invariants: idempotent (IF NOT EXISTS); PublicId is NOT NULL DEFAULT NEWID(), so a full unique index (no filter).
-- Sprocs:
-- U-699: seek support for ReadBillByPublicId and every service-layer read_by_public_id.
-- Idempotent migration slice; mirrored in dbo.bill.sql for from-scratch builds.
-- PublicId is NOT NULL DEFAULT NEWID() on dbo.Bill — full unique index (no filter).

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
       FROM sys.indexes
       WHERE name = 'UQ_Bill_PublicId'
         AND object_id = OBJECT_ID('dbo.Bill')
   )
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UQ_Bill_PublicId]
        ON [dbo].[Bill] ([PublicId]);
END
GO
