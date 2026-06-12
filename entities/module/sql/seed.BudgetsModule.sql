-- Add the Budgets module so the Budget / BudgetRevision / BudgetLineItem
-- routers can gate on Modules.BUDGETS and grant migrations can hang
-- RoleModule rows off it. The Route powers the React nav entry.
--
-- Run this single-module seed — do NOT re-run seed.AllModules.sql for
-- this (it re-seeds ghost modules).
--
-- Idempotent: only inserts if missing.

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Budgets')
BEGIN
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Budgets', '/budget/list');
END
GO
