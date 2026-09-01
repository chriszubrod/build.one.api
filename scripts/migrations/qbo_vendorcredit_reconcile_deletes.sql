-- VendorCredit reconcile-deletes support (2026-06-20).
-- Adds the delete sproc the auto-delete-on-QBO-deletion path needs, in dbo
-- (call_procedure issues EXEC dbo.{name}). Idempotent.
--   python scripts/run_sql.py scripts/migrations/qbo_vendorcredit_reconcile_deletes.sql
--
-- U-353: the DeleteVendorCreditBillCreditByQboVendorCreditId body this file used to
-- ALSO declare (a known-dup of qbo.vendorcredit_bill_credit.sql's copy, per
-- tests/sproc_drift_ledger.py) was removed here — qbo.VendorCreditBillCredit is
-- retired; the service deletes the BillCredit directly via dbo-native identity now.

-- Staging-header delete. FK_QboVendorCreditLine_QboVendorCredit ON DELETE CASCADE
-- removes the qbo.VendorCreditLine rows; the BillCredit is deleted by the service
-- BEFORE this call.
CREATE OR ALTER PROCEDURE DeleteQboVendorCreditByQboId
    @QboId NVARCHAR(50)
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM [qbo].[VendorCredit] WHERE [QboId] = @QboId;
END
GO
