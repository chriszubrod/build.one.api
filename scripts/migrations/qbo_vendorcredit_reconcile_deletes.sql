-- VendorCredit reconcile-deletes support (2026-06-20).
-- Adds the two delete sprocs the auto-delete-on-QBO-deletion path needs, in dbo
-- (call_procedure issues EXEC dbo.{name}; the pre-existing
-- [qbo].[DeleteVendorCreditBillCreditByQboVendorCreditId] was unreachable). Idempotent.
--   python scripts/run_sql.py scripts/migrations/qbo_vendorcredit_reconcile_deletes.sql

-- Staging-header delete. FK_QboVendorCreditLine_QboVendorCredit ON DELETE CASCADE
-- removes the qbo.VendorCreditLine rows; the BillCredit + line mappings are deleted
-- by the service BEFORE this call.
CREATE OR ALTER PROCEDURE DeleteQboVendorCreditByQboId
    @QboId NVARCHAR(50)
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM [qbo].[VendorCredit] WHERE [QboId] = @QboId;
END
GO

-- BillCredit-mapping delete by the local qbo.VendorCredit PK (recreated in dbo).
CREATE OR ALTER PROCEDURE DeleteVendorCreditBillCreditByQboVendorCreditId
    @QboVendorCreditId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM [qbo].[VendorCreditBillCredit] WHERE [QboVendorCreditId] = @QboVendorCreditId;
END
GO
