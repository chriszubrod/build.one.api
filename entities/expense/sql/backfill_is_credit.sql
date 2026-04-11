-- Migration: Backfill IsCredit on dbo.Expense from qbo.Purchase.Credit
-- Purpose: Set IsCredit=1 for all Expenses that originated from QBO CreditCardCredits
-- Run with: python scripts/run_sql.py entities/expense/sql/backfill_is_credit.sql
-- Run AFTER: add_is_credit_column.sql

DECLARE @RowsUpdated INT;

UPDATE e
SET e.[IsCredit] = 1
FROM [dbo].[Expense] e
INNER JOIN [qbo].[PurchaseExpense] pe ON pe.[ExpenseId] = e.[Id]
INNER JOIN [qbo].[Purchase] p ON p.[Id] = pe.[QboPurchaseId]
WHERE p.[Credit] = 1
  AND e.[IsCredit] = 0;

SET @RowsUpdated = @@ROWCOUNT;
PRINT 'Backfilled IsCredit=1 on ' + CAST(@RowsUpdated AS VARCHAR(10)) + ' Expense record(s)';
GO
