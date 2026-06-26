-- Bill external links (2026-06-25) — surface QBO + Box deep links from the
-- /bill/{public_id} React detail page so AP can jump straight to QuickBooks
-- and to the Box folder + budget tracker for each line item's project.
--
-- Two new read sprocs. Both idempotent (`CREATE OR ALTER`); both side-effect-free.
-- They do NOT touch dbo.[Bill] / dbo.[BillLineItem] schemas — pure look-aside
-- reads layered on top of the existing endpoints. The router calls one per
-- /bill/{public_id} request (QBO) and one per /bill_line_items/bill/{id}
-- request (Box) and merges the results into the response shape.
--
-- KEYSPACE DISCIPLINE (per feedback_qbo_dbo_id_keyspaces.md + box.folder.sql header):
--   - `qbo.[Bill].QboId`         NVARCHAR — Intuit's bill id (use in URLs)
--   - `qbo.[Bill].RealmId`       NVARCHAR — Intuit's realm id (use in URLs)
--   - `qbo.[Bill].Id`            BIGINT   — local staging-table PK, NOT exposed
--   - `[box].[Folder].BoxFolderId` NVARCHAR — Box's string folder id (use in URLs)
--   - `[box].[Folder].Id`          BIGINT   — local PK, NOT exposed
--   - `[box].[ProjectWorkbook].BoxFileId` NVARCHAR — Box's string file id
--
-- Run: `python scripts/run_sql.py scripts/migrations/bill_external_links.sql`

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- =====================================================================
-- ReadBillQboLinkInfo — bill-level (one QBO bill per dbo.Bill in practice).
-- =====================================================================
-- Walks dbo.Bill → dbo.BillLineItem → qbo.BillLineItemBillLine →
-- qbo.BillLine → qbo.Bill to fetch the Intuit (QboId, RealmId) used to
-- construct the QBO web URL. Returns at most one row.
--
-- A bill is "synced" once at least one line item has been pushed to QBO.
-- We pick the qbo.Bill with the lowest Id (oldest) to be stable across
-- re-sync churn. Returns empty if no line item is mapped yet.

CREATE OR ALTER PROCEDURE dbo.ReadBillQboLinkInfo (@BillId BIGINT)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1
        qb.[QboId]    AS QboId,
        qb.[RealmId]  AS QboRealmId
    FROM dbo.[BillLineItem] bli
    INNER JOIN qbo.[BillLineItemBillLine] map ON map.[BillLineItemId] = bli.[Id]
    INNER JOIN qbo.[BillLine] qbline           ON qbline.[Id] = map.[QboBillLineId]
    INNER JOIN qbo.[Bill] qb                   ON qb.[Id] = qbline.[QboBillId]
    WHERE bli.[BillId] = @BillId
      AND qb.[QboId] IS NOT NULL
    ORDER BY qb.[Id] ASC;
END;
GO

-- =====================================================================
-- ReadBillLineItemBoxLinks — per-line-item (multi-project bills supported).
-- =====================================================================
-- Returns one row per dbo.BillLineItem on the bill, including line items
-- whose project has no Box mapping (LEFT JOIN — Box columns NULL in that
-- case). The router merges per-row results back into the line-item list
-- by BillLineItemId so the React table can show or hide icons per row.
--
-- Doc class for bills is fixed at 'invoices' (the project's `14 - Invoices`
-- Box folder). Workbook is one per project (UNIQUE on ProjectId).

CREATE OR ALTER PROCEDURE dbo.ReadBillLineItemBoxLinks (@BillId BIGINT)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        bli.[Id]                AS BillLineItemId,
        f.[BoxFolderId]         AS BoxInvoicesFolderId,
        pw.[BoxFileId]          AS BoxWorkbookFileId,
        pw.[WorksheetName]      AS BoxWorkbookWorksheetName
    FROM dbo.[BillLineItem] bli
    LEFT JOIN [box].[ProjectFolder] pf
        ON pf.[ProjectId] = bli.[ProjectId]
       AND pf.[DocClass]  = N'invoices'
    LEFT JOIN [box].[Folder] f
        ON f.[Id] = pf.[BoxFolderId]
    LEFT JOIN [box].[ProjectWorkbook] pw
        ON pw.[ProjectId] = bli.[ProjectId]
    WHERE bli.[BillId] = @BillId;
END;
GO

PRINT 'bill_external_links migration applied (ReadBillQboLinkInfo + ReadBillLineItemBoxLinks).';
