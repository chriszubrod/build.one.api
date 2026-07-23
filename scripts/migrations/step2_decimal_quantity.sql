-- Step 2: line-item Quantity INT -> DECIMAL(18,4) (fractional qty support).
-- This file now owns only the two Quantity column widenings below. The 4
-- Create/Update sproc bodies it once re-issued live in their entity base files
-- (see the SUPERSEDED pointer stubs, U-074/U-111). Idempotent.

IF EXISTS (SELECT 1 FROM sys.columns col JOIN sys.types t ON col.user_type_id=t.user_type_id WHERE col.object_id=OBJECT_ID('dbo.BillLineItem') AND col.name='Quantity' AND t.name='int') ALTER TABLE dbo.[BillLineItem] ALTER COLUMN [Quantity] DECIMAL(18,4) NULL;
GO

IF EXISTS (SELECT 1 FROM sys.columns col JOIN sys.types t ON col.user_type_id=t.user_type_id WHERE col.object_id=OBJECT_ID('dbo.ExpenseLineItem') AND col.name='Quantity' AND t.name='int') ALTER TABLE dbo.[ExpenseLineItem] ALTER COLUMN [Quantity] DECIMAL(18,4) NULL;
GO

-- ===== 7. CreateBillLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-074, 2026-07-17) - body relocated to the entity base, not lost.
--
-- This copy was already DECIMAL(18,4) + @CreatedByUserId-threaded (the canonical
-- form). U-074 relocated that exact body to its single home:
--   entities/bill_line_item/sql/dbo.bill_line_item.sql
-- The @Quantity column widening (the ALTER TABLE at the top of this file) is
-- still owned here. Re-running this file is now a no-op for CreateBillLineItem.
-- Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== UpdateBillLineItemById =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-111, 2026-07-22) — body relocated to the entity base, not lost.
--
-- The LIVE unconditional-SET form (SubCostCodeId / ProjectId) now lives in its
-- single home:
--   entities/bill_line_item/sql/dbo.bill_line_item.sql
-- Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 9. CreateExpenseLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-074, 2026-07-17) - body relocated to the entity base, not lost.
--
-- This copy was already DECIMAL(18,4) + @CreatedByUserId-threaded (the canonical
-- form). U-074 relocated that exact body to its single home:
--   entities/expense_line_item/sql/dbo.expense_line_item.sql
-- The @Quantity column widening (the ALTER TABLE at the top of this file) is
-- still owned here. Re-running this file is now a no-op for CreateExpenseLineItem.
-- Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== UpdateExpenseLineItemById =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-111, 2026-07-22) — body relocated to the entity base, not lost.
--
-- The LIVE unconditional-SET form (SubCostCodeId / ProjectId) now lives in its
-- single home:
--   entities/expense_line_item/sql/dbo.expense_line_item.sql
-- Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO
