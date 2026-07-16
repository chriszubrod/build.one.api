-- Phase 5 — Multi-tenant scoping. Backfill CompanyId across 23 tables.
-- Idempotent: only updates rows where CompanyId IS NULL.
--
-- Order matters:
--   1. Project — backfill from single existing Company.
--   2. Tables with ProjectId on the row — derive via Project.CompanyId.
--   3. Tables without ProjectId — backfill to single Company.
--   4. Attachment link tables — derive via parent line item ProjectId.
--   5. Attachment proper — derive via link tables, fall back to single Company.
--
-- With one Company today, every backfill resolves to the same Id.
-- Walking the parent chain anyway preserves data-model integrity for
-- the day a second Company appears (or for spot-checking via
-- "is this row's CompanyId consistent with its Project's CompanyId?").

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

DECLARE @DefaultCompanyId BIGINT;
SELECT TOP 1 @DefaultCompanyId = [Id]
  FROM dbo.[Company]
 ORDER BY [Id] ASC;

IF @DefaultCompanyId IS NULL
BEGIN
    RAISERROR('No Company row found — Phase 5 backfill cannot proceed.', 16, 1);
    RETURN;
END

PRINT CONCAT('Backfilling CompanyId from default Company.Id = ', @DefaultCompanyId);

-- 1. Project (the bridge)
UPDATE dbo.[Project] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('Project: ', @@ROWCOUNT, ' row(s) backfilled.');

-- 2a. Tables with ProjectId on the row
UPDATE i SET [CompanyId] = p.[CompanyId]
  FROM dbo.[Invoice] i
  JOIN dbo.[Project] p ON p.[Id] = i.[ProjectId]
 WHERE i.[CompanyId] IS NULL;
PRINT CONCAT('Invoice: ', @@ROWCOUNT, ' row(s) backfilled via Project.');

-- TimeEntry carries no ProjectId of its own -- the project lives on TimeLog,
-- one per clock-in segment. Every row therefore takes the default Company.
-- This is what actually ran historically: the old bridge-via-Project UPDATE
-- keyed on TimeEntry.ProjectId matched zero rows and this statement did all
-- the work.
--
-- NB: a bridge IS reachable (TimeEntry -> TimeLog.ProjectId -> Project.CompanyId),
-- but it is deliberately not used here: a single TimeEntry can span TimeLogs on
-- projects in different Companies, so there is no single Company to derive. If
-- Phase 5b multi-tenant enforcement lands, that ambiguity must be resolved
-- before this can be anything other than the default.
UPDATE te SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[TimeEntry] te
 WHERE te.[CompanyId] IS NULL;
PRINT CONCAT('TimeEntry: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE cl SET [CompanyId] = p.[CompanyId]
  FROM dbo.[ContractLabor] cl
  JOIN dbo.[Project] p ON p.[Id] = cl.[ProjectId]
 WHERE cl.[CompanyId] IS NULL
   AND cl.[ProjectId] IS NOT NULL;
PRINT CONCAT('ContractLabor (with ProjectId): ', @@ROWCOUNT, ' row(s) backfilled via Project.');

UPDATE cl SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[ContractLabor] cl
 WHERE cl.[CompanyId] IS NULL;
PRINT CONCAT('ContractLabor (null-ProjectId fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE bli SET [CompanyId] = p.[CompanyId]
  FROM dbo.[BillLineItem] bli
  JOIN dbo.[Project] p ON p.[Id] = bli.[ProjectId]
 WHERE bli.[CompanyId] IS NULL
   AND bli.[ProjectId] IS NOT NULL;
PRINT CONCAT('BillLineItem (with ProjectId): ', @@ROWCOUNT, ' row(s) backfilled via Project.');

UPDATE bli SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[BillLineItem] bli
 WHERE bli.[CompanyId] IS NULL;
PRINT CONCAT('BillLineItem (null-ProjectId fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE bcli SET [CompanyId] = p.[CompanyId]
  FROM dbo.[BillCreditLineItem] bcli
  JOIN dbo.[Project] p ON p.[Id] = bcli.[ProjectId]
 WHERE bcli.[CompanyId] IS NULL
   AND bcli.[ProjectId] IS NOT NULL;
PRINT CONCAT('BillCreditLineItem (with ProjectId): ', @@ROWCOUNT, ' row(s) backfilled via Project.');

UPDATE bcli SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[BillCreditLineItem] bcli
 WHERE bcli.[CompanyId] IS NULL;
PRINT CONCAT('BillCreditLineItem (null-ProjectId fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE eli SET [CompanyId] = p.[CompanyId]
  FROM dbo.[ExpenseLineItem] eli
  JOIN dbo.[Project] p ON p.[Id] = eli.[ProjectId]
 WHERE eli.[CompanyId] IS NULL
   AND eli.[ProjectId] IS NOT NULL;
PRINT CONCAT('ExpenseLineItem (with ProjectId): ', @@ROWCOUNT, ' row(s) backfilled via Project.');

UPDATE eli SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[ExpenseLineItem] eli
 WHERE eli.[CompanyId] IS NULL;
PRINT CONCAT('ExpenseLineItem (null-ProjectId fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE clli SET [CompanyId] = p.[CompanyId]
  FROM dbo.[ContractLaborLineItem] clli
  JOIN dbo.[Project] p ON p.[Id] = clli.[ProjectId]
 WHERE clli.[CompanyId] IS NULL
   AND clli.[ProjectId] IS NOT NULL;
PRINT CONCAT('ContractLaborLineItem (with ProjectId): ', @@ROWCOUNT, ' row(s) backfilled via Project.');

UPDATE clli SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[ContractLaborLineItem] clli
 WHERE clli.[CompanyId] IS NULL;
PRINT CONCAT('ContractLaborLineItem (null-ProjectId fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE tl SET [CompanyId] = p.[CompanyId]
  FROM dbo.[TimeLog] tl
  JOIN dbo.[Project] p ON p.[Id] = tl.[ProjectId]
 WHERE tl.[CompanyId] IS NULL
   AND tl.[ProjectId] IS NOT NULL;
PRINT CONCAT('TimeLog (with ProjectId): ', @@ROWCOUNT, ' row(s) backfilled via Project.');

UPDATE tl SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[TimeLog] tl
 WHERE tl.[CompanyId] IS NULL;
PRINT CONCAT('TimeLog (null-ProjectId fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

-- 2b. InvoiceLineItem — no ProjectId on row, derive via Invoice
UPDATE ili SET [CompanyId] = i.[CompanyId]
  FROM dbo.[InvoiceLineItem] ili
  JOIN dbo.[Invoice] i ON i.[Id] = ili.[InvoiceId]
 WHERE ili.[CompanyId] IS NULL;
PRINT CONCAT('InvoiceLineItem: ', @@ROWCOUNT, ' row(s) backfilled via Invoice.');

-- 3. Vendor-keyed parents — no Project on parent
UPDATE [dbo].[Bill] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('Bill: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE [dbo].[BillCredit] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('BillCredit: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE [dbo].[Expense] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('Expense: ', @@ROWCOUNT, ' row(s) backfilled to default.');

-- 4. Attachment link tables — derive via parent line item
UPDATE bla SET [CompanyId] = bli.[CompanyId]
  FROM dbo.[BillLineItemAttachment] bla
  JOIN dbo.[BillLineItem] bli ON bli.[Id] = bla.[BillLineItemId]
 WHERE bla.[CompanyId] IS NULL
   AND bli.[CompanyId] IS NOT NULL;
PRINT CONCAT('BillLineItemAttachment (via BillLineItem): ', @@ROWCOUNT, ' row(s) backfilled.');

UPDATE bla SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[BillLineItemAttachment] bla
 WHERE bla.[CompanyId] IS NULL;
PRINT CONCAT('BillLineItemAttachment (fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE ela SET [CompanyId] = eli.[CompanyId]
  FROM dbo.[ExpenseLineItemAttachment] ela
  JOIN dbo.[ExpenseLineItem] eli ON eli.[Id] = ela.[ExpenseLineItemId]
 WHERE ela.[CompanyId] IS NULL
   AND eli.[CompanyId] IS NOT NULL;
PRINT CONCAT('ExpenseLineItemAttachment (via ExpenseLineItem): ', @@ROWCOUNT, ' row(s) backfilled.');

UPDATE ela SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[ExpenseLineItemAttachment] ela
 WHERE ela.[CompanyId] IS NULL;
PRINT CONCAT('ExpenseLineItemAttachment (fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE ila SET [CompanyId] = ili.[CompanyId]
  FROM dbo.[InvoiceLineItemAttachment] ila
  JOIN dbo.[InvoiceLineItem] ili ON ili.[Id] = ila.[InvoiceLineItemId]
 WHERE ila.[CompanyId] IS NULL
   AND ili.[CompanyId] IS NOT NULL;
PRINT CONCAT('InvoiceLineItemAttachment (via InvoiceLineItem): ', @@ROWCOUNT, ' row(s) backfilled.');

UPDATE ila SET [CompanyId] = @DefaultCompanyId
  FROM dbo.[InvoiceLineItemAttachment] ila
 WHERE ila.[CompanyId] IS NULL;
PRINT CONCAT('InvoiceLineItemAttachment (fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

-- 5. Attachment proper — derive via any link table, else default
WITH attachment_company AS (
    SELECT a.[Id] AS AttachmentId, MIN(t.[CompanyId]) AS CompanyId
      FROM dbo.[Attachment] a
      LEFT JOIN dbo.[BillLineItemAttachment] bla ON bla.[AttachmentId] = a.[Id]
      LEFT JOIN dbo.[ExpenseLineItemAttachment] ela ON ela.[AttachmentId] = a.[Id]
      LEFT JOIN dbo.[InvoiceLineItemAttachment] ila ON ila.[AttachmentId] = a.[Id]
      OUTER APPLY (
        SELECT bla.[CompanyId] AS CompanyId WHERE bla.[CompanyId] IS NOT NULL
        UNION ALL
        SELECT ela.[CompanyId] WHERE ela.[CompanyId] IS NOT NULL
        UNION ALL
        SELECT ila.[CompanyId] WHERE ila.[CompanyId] IS NOT NULL
      ) t
     WHERE a.[CompanyId] IS NULL
     GROUP BY a.[Id]
)
UPDATE a SET [CompanyId] = ac.[CompanyId]
  FROM dbo.[Attachment] a
  JOIN attachment_company ac ON ac.AttachmentId = a.[Id]
 WHERE a.[CompanyId] IS NULL
   AND ac.[CompanyId] IS NOT NULL;
PRINT CONCAT('Attachment (via link tables): ', @@ROWCOUNT, ' row(s) backfilled.');

UPDATE [dbo].[Attachment] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('Attachment (fallback): ', @@ROWCOUNT, ' row(s) backfilled to default.');

-- 6. Email pipeline / Review / Bill folder — single Company
UPDATE [dbo].[EmailMessage] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('EmailMessage: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE [dbo].[EmailAttachment] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('EmailAttachment: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE [dbo].[ReviewStatus] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('ReviewStatus: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE [dbo].[ReviewEntry] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('ReviewEntry: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE [dbo].[BillFolderRun] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('BillFolderRun: ', @@ROWCOUNT, ' row(s) backfilled to default.');

UPDATE [dbo].[BillFolderRunItem] SET [CompanyId] = @DefaultCompanyId WHERE [CompanyId] IS NULL;
PRINT CONCAT('BillFolderRunItem: ', @@ROWCOUNT, ' row(s) backfilled to default.');

PRINT 'Phase 5 backfill complete.';
