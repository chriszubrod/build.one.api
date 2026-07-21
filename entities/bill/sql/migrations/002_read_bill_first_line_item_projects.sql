-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc body removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   ReadBillFirstLineItemProjects — batch lookup of the first line item's
--   ProjectId per Bill (comma-separated @BillIds) for the React Bills list.
--
-- The canonical definition now lives in exactly ONE place:
--   entities/bill/sql/dbo.bill.sql
--
-- Sproc formerly redefined here: dbo.ReadBillFirstLineItemProjects
--
-- Re-running this file is now a no-op. Do NOT reintroduce a body here.
--
-- DANGER (motivated U-100): re-applying would redefine ReadBillFirstLineItemProjects
-- outside its entity base file, reintroducing single-source drift.
-- ---------------------------------------------------------------------------

GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definition lives in entities/bill/sql/dbo.bill.sql.';
