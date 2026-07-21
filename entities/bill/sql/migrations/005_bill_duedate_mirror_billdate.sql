-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Bill.DueDate must always equal Bill.BillDate (authoritative rule confirmed
--   2026-05-27). Force it at the sproc choke point so all callers — manual UI,
--   email/bill agents, QBO sync, scripts — are covered by one change.
--   @DueDate parameter is kept for signature back-compat but its value is
--   ignored; DueDate is always persisted as @BillDate instead.
--   Idempotent (CREATE OR ALTER).
--
-- The canonical definitions now live in exactly ONE place each:
--   dbo.CreateBill      → entities/bill/sql/dbo.bill_create_source_email.sql
--   dbo.UpdateBillById  → entities/bill/sql/dbo.bill.sql
--
-- Sprocs formerly redefined here: CreateBill, UpdateBillById
--
-- Re-running this file is now a no-op. Do NOT reintroduce bodies here.
--
-- DANGER (motivated U-100): bodies here currently match the canonical base files;
-- re-applying would split DueDate-mirror maintenance across migration + base and
-- risk a future drifted copy reverting DueDate=@DueDate on UpdateBillById.
-- ---------------------------------------------------------------------------

GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definitions live in entities/bill/sql/dbo.bill_create_source_email.sql and entities/bill/sql/dbo.bill.sql.';
