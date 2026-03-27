-- Cleanup: Remove orphaned qbo.PurchaseLineExpenseLineItem rows where the
-- referenced ExpenseLineItem no longer exists in dbo.ExpenseLineItem.
--
-- These orphans were created before the fix that ensures the mapping is
-- deleted before the ExpenseLineItem. They are safe to remove because the
-- ExpenseLineItem is already gone; on the next sync the QboPurchaseLine will
-- be re-synced and a new ExpenseLineItem + mapping will be created.
--
-- Run with: python scripts/run_sql.py integrations/intuit/qbo/purchase/sql/cleanup_orphaned_line_mappings.sql

DECLARE @deleted INT;

DELETE FROM [qbo].[PurchaseLineExpenseLineItem]
WHERE [ExpenseLineItemId] NOT IN (SELECT [Id] FROM [dbo].[ExpenseLineItem]);

SET @deleted = @@ROWCOUNT;
PRINT CAST(@deleted AS NVARCHAR(20)) + ' orphaned PurchaseLineExpenseLineItem row(s) deleted.';
GO

-- Re-add the FK constraint that was blocked by the orphans.
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
