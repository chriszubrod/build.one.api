-- ---------------------------------------------------------------------------
-- U-646 (2026-10-11) — ExpenseLineItem human Billable decision audit columns.
-- Records who decided Billable and when so the QBO purchase pull cannot
-- overwrite an operator's choice on subsequent sync ticks.
--
-- DDL ONLY. Proc bodies live in entities/expense_line_item/sql/dbo.expense_line_item.sql,
-- the single source of truth (tests/test_sproc_single_source.py). Do not add
-- procedure definitions to this migration.
-- ---------------------------------------------------------------------------

IF COL_LENGTH('dbo.ExpenseLineItem', 'BillableDecidedByUserId') IS NULL
BEGIN
    ALTER TABLE dbo.[ExpenseLineItem]
        ADD [BillableDecidedByUserId] BIGINT NULL;
END
GO

IF COL_LENGTH('dbo.ExpenseLineItem', 'BillableDecidedAt') IS NULL
BEGIN
    ALTER TABLE dbo.[ExpenseLineItem]
        ADD [BillableDecidedAt] DATETIME2(3) NULL;
END
GO
