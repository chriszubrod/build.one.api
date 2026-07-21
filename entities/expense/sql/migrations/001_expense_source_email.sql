-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc body removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   001_expense_source_email.sql
--   Unit 1 (ExpenseAgent / BillAgent parity): thread SourceEmailMessageId through
--   dbo.CreateExpense so receipt-intake (email + folder) can preserve the
--   source-email audit trail, mirroring dbo.CreateBill (gap2_core_threading.sql).
--
--   The [SourceEmailMessageId] BIGINT NULL column + FK + index already exist on
--   dbo.Expense (entities/email_message/sql/dbo.source_email_message_fk.sql); this
--   migration only extends the sproc to accept, INSERT, and OUTPUT it. The gap2
--   @CreatedByUserId threading + COALESCE(@CreatedByUserId, 17) system-context
--   fallback are preserved verbatim. Idempotent (CREATE OR ALTER).
--
-- The canonical definition now lives in exactly ONE place:
--   entities/expense/sql/dbo.expense.sql
--
-- Sproc formerly redefined here: dbo.CreateExpense
--
-- Re-running this file is now a no-op. Do NOT reintroduce a body here.
--
-- DANGER (motivated U-100): body here currently matches the canonical base file;
-- re-applying would split CreateExpense maintenance across migration + base.
-- ---------------------------------------------------------------------------

GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definition lives in entities/expense/sql/dbo.expense.sql.';
