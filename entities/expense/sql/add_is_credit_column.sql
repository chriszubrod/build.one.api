-- Migration: Add IsCredit column to dbo.Expense
-- Purpose: Distinguish CreditCardCredits (QBO Purchase with Credit=true) from regular Expenses
-- Run with: python scripts/run_sql.py entities/expense/sql/add_is_credit_column.sql

-- ============================================================================
-- 1. Add column
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'IsCredit')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [IsCredit] BIT NOT NULL DEFAULT 0;
    PRINT 'Added IsCredit column to dbo.Expense';
END
ELSE
    PRINT 'IsCredit column already exists';
GO


-- ============================================================================
-- 2. Update stored procedures
-- ============================================================================

-- SUPERSEDED (U-100): dbo.CreateExpense single-sourced in entities/expense/sql/dbo.expense.sql.
-- DANGER: its CreateExpense drops @SourceEmailMessageId + @CreatedByUserId.
GO

-- SUPERSEDED (U-100): dbo.ReadExpenses single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-100): dbo.ReadExpenseById single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-100): dbo.ReadExpenseByPublicId single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-100): dbo.ReadExpenseByReferenceNumberAndVendorId single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-100): dbo.UpdateExpenseById single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-100): dbo.DeleteExpenseById single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-100): dbo.ReadExpensesPaginated single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-100): dbo.CountExpenses single-sourced in entities/expense/sql/dbo.expense.sql.
GO
