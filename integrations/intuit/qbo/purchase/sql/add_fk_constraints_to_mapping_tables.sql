-- Migration: Add FK constraints to Purchase mapping tables
-- Purpose: Enforce referential integrity between mapping tables and their source/target tables.
--          Prevents orphaned mapping rows when parent records are deleted.
-- Run with: python scripts/run_sql.py integrations/intuit/qbo/purchase/sql/add_fk_constraints_to_mapping_tables.sql

-- ============================================================================
-- 1. qbo.PurchaseExpense FK constraints
-- ============================================================================

-- FK: QboPurchaseId -> qbo.Purchase(Id) with CASCADE DELETE
-- When a QboPurchase is deleted, its mapping row is automatically removed.
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_PurchaseExpense_QboPurchase'
)
BEGIN
    ALTER TABLE [qbo].[PurchaseExpense]
    ADD CONSTRAINT [FK_PurchaseExpense_QboPurchase]
        FOREIGN KEY ([QboPurchaseId])
        REFERENCES [qbo].[Purchase]([Id])
        ON DELETE CASCADE;
    PRINT 'Added FK_PurchaseExpense_QboPurchase (CASCADE DELETE)';
END
ELSE
    PRINT 'FK_PurchaseExpense_QboPurchase already exists';
GO

-- FK: ExpenseId -> dbo.Expense(Id) with NO ACTION
-- Application code must delete the mapping before deleting the Expense.
-- NO ACTION prevents accidental orphaning of QBO sync data.
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_PurchaseExpense_Expense'
)
BEGIN
    ALTER TABLE [qbo].[PurchaseExpense]
    ADD CONSTRAINT [FK_PurchaseExpense_Expense]
        FOREIGN KEY ([ExpenseId])
        REFERENCES [dbo].[Expense]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_PurchaseExpense_Expense (NO ACTION)';
END
ELSE
    PRINT 'FK_PurchaseExpense_Expense already exists';
GO


-- ============================================================================
-- 2. qbo.PurchaseLineExpenseLineItem FK constraints
-- ============================================================================

-- FK: QboPurchaseLineId -> qbo.PurchaseLine(Id) with CASCADE DELETE
-- When a QboPurchaseLine is deleted (stale line cleanup), its mapping row is automatically removed.
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_PurchaseLineExpenseLineItem_QboPurchaseLine'
)
BEGIN
    ALTER TABLE [qbo].[PurchaseLineExpenseLineItem]
    ADD CONSTRAINT [FK_PurchaseLineExpenseLineItem_QboPurchaseLine]
        FOREIGN KEY ([QboPurchaseLineId])
        REFERENCES [qbo].[PurchaseLine]([Id])
        ON DELETE CASCADE;
    PRINT 'Added FK_PurchaseLineExpenseLineItem_QboPurchaseLine (CASCADE DELETE)';
END
ELSE
    PRINT 'FK_PurchaseLineExpenseLineItem_QboPurchaseLine already exists';
GO

-- FK: ExpenseLineItemId -> dbo.ExpenseLineItem(Id) with NO ACTION
-- Application code must delete the mapping before deleting the ExpenseLineItem.
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_PurchaseLineExpenseLineItem_ExpenseLineItem'
)
BEGIN
    ALTER TABLE [qbo].[PurchaseLineExpenseLineItem]
    ADD CONSTRAINT [FK_PurchaseLineExpenseLineItem_ExpenseLineItem]
        FOREIGN KEY ([ExpenseLineItemId])
        REFERENCES [dbo].[ExpenseLineItem]([Id])
        ON DELETE NO ACTION;
    PRINT 'Added FK_PurchaseLineExpenseLineItem_ExpenseLineItem (NO ACTION)';
END
ELSE
    PRINT 'FK_PurchaseLineExpenseLineItem_ExpenseLineItem already exists';
GO


-- ============================================================================
-- 3. Cleanup: Remove any orphaned mapping rows before constraints are enforced
--    (Run this section BEFORE the ALTER TABLE statements if needed)
-- ============================================================================

-- Check for orphaned PurchaseExpense rows (QboPurchase deleted)
-- DELETE FROM [qbo].[PurchaseExpense]
-- WHERE [QboPurchaseId] NOT IN (SELECT [Id] FROM [qbo].[Purchase]);

-- Check for orphaned PurchaseExpense rows (Expense deleted)
-- DELETE FROM [qbo].[PurchaseExpense]
-- WHERE [ExpenseId] NOT IN (SELECT [Id] FROM [dbo].[Expense]);

-- Check for orphaned PurchaseLineExpenseLineItem rows (QboPurchaseLine deleted)
-- DELETE FROM [qbo].[PurchaseLineExpenseLineItem]
-- WHERE [QboPurchaseLineId] NOT IN (SELECT [Id] FROM [qbo].[PurchaseLine]);

-- Check for orphaned PurchaseLineExpenseLineItem rows (ExpenseLineItem deleted)
-- DELETE FROM [qbo].[PurchaseLineExpenseLineItem]
-- WHERE [ExpenseLineItemId] NOT IN (SELECT [Id] FROM [dbo].[ExpenseLineItem]);
