-- QBO VendorCredit duplication fix (2026-06-18)
-- Root cause: VendorCredit -> BillCredit line sync is delete-then-recreate. When a
-- BillCreditLineItem had been billed onto a customer Invoice (InvoiceLineItem
-- .BillCreditLineItemId FK set), the delete was FK-blocked, the exception was
-- swallowed, and the line was re-created from QBO -> a DUPLICATE on every re-pull.
--
-- Fix: nullify the InvoiceLineItem FK before deleting the BillCreditLineItem (the
-- service now calls this), so the delete always succeeds. Mirrors the existing
-- NullifyInvoiceLineItemsByBillLineItemId sproc. Idempotent (CREATE OR ALTER).
--
-- Run with: python scripts/run_sql.py scripts/migrations/qbo_vendorcredit_dup_fix.sql

CREATE OR ALTER PROCEDURE NullifyInvoiceLineItemsByBillCreditLineItemId
(
    @BillCreditLineItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[InvoiceLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillCreditLineItemId] = NULL
    WHERE [BillCreditLineItemId] = @BillCreditLineItemId;

    COMMIT TRANSACTION;
END;
GO
