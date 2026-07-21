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
-- (as of U-102 this file applies NOTHING — see the stub below; the run PRINTs
--  a SUPERSEDED line so a no-op is distinguishable from a failed apply.)

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-102, 2026-07-21): dbo.NullifyInvoiceLineItemsByBillCreditLineItemId
-- single-sourced in entities/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql.
-- The body there is identical to the one this file carried (no drift), so this
-- removal is behavior-neutral. Re-running this file is now a no-op. Do NOT
-- reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

PRINT 'SUPERSEDED (U-102): no sprocs applied; dbo.NullifyInvoiceLineItemsByBillCreditLineItemId lives in entities/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql.';
