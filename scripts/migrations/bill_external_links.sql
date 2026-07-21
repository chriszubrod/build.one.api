-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-100, 2026-07-21) — sproc bodies removed, NOT the intent.
--
-- Original intent of this file (preserved for lineage):
--   Bill external links (2026-06-25) — surface QBO + Box deep links from the
--   /bill/{public_id} React detail page so AP can jump straight to QuickBooks
--   and to the Box folder + budget tracker for each line item's project.
--
--   Two new read sprocs. Both idempotent (`CREATE OR ALTER`); both side-effect-free.
--   They do NOT touch dbo.[Bill] / dbo.[BillLineItem] schemas — pure look-aside
--   reads layered on top of the existing endpoints. The router calls one per
--   /bill/{public_id} request (QBO) and one per /bill_line_items/bill/{id}
--   request (Box) and merges the results into the response shape.
--
--   KEYSPACE DISCIPLINE (per feedback_qbo_dbo_id_keyspaces.md + box.folder.sql header):
--     - `qbo.[Bill].QboId`         NVARCHAR — Intuit's bill id (use in URLs)
--     - `qbo.[Bill].RealmId`       NVARCHAR — Intuit's realm id (use in URLs)
--     - `qbo.[Bill].Id`            BIGINT   — local staging-table PK, NOT exposed
--     - `[box].[Folder].BoxFolderId` NVARCHAR — Box's string folder id (use in URLs)
--     - `[box].[Folder].Id`          BIGINT   — local PK, NOT exposed
--     - `[box].[ProjectWorkbook].BoxFileId` NVARCHAR — Box's string file id
--
--   Run: `python scripts/run_sql.py scripts/migrations/bill_external_links.sql`
--
-- The canonical definition of these sprocs now lives in exactly ONE place each:
--   dbo.ReadBillQboLinkInfo      → entities/bill/sql/dbo.bill.sql
--   dbo.ReadBillLineItemBoxLinks → entities/bill_line_item/sql/dbo.bill_line_item.sql
--
-- Re-running this file is now a no-op. Do NOT reintroduce a body here.
--
-- DANGER (motivated U-100): re-applying would redefine the two read sprocs outside
-- their entity base files, reintroducing single-source drift on the QBO/Box link
-- look-aside reads the React bill detail page depends on.
-- ---------------------------------------------------------------------------

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

PRINT 'SUPERSEDED (U-100): no sprocs applied; canonical definitions live in entities/bill/sql/dbo.bill.sql and entities/bill_line_item/sql/dbo.bill_line_item.sql.';
