-- U-187: sync-proof VendorInvoiceNumber on dbo.Attachment
--
-- Adds a NVARCHAR(100) column that holds the vendor invoice number parsed from
-- the attachment's OWN extracted text. Unlike dbo.Expense.ReferenceNumber, the
-- QBO purchase->Expense connector never writes dbo.Attachment, so this value is
-- immune to the KI-42 scheduler clobber that reverts hand edits every pull.
--
-- Idempotent (IF NOT EXISTS). Apply this BEFORE re-applying the base file
-- entities/attachment/sql/dbo.attachment.sql (whose Read* sprocs + the
-- UpdateAttachmentExtraction setter in add_extraction_fields.sql now reference
-- the column) — same layering the extraction columns already follow. No sproc
-- bodies here (the setter lives in add_extraction_fields.sql; the Read* SELECTs
-- in the base file).

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'VendorInvoiceNumber'
)
BEGIN
    ALTER TABLE dbo.[Attachment]
    ADD [VendorInvoiceNumber] NVARCHAR(100) NULL;
END
GO

-- Index for the audit-side lookups (source line -> attachment -> number).
IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = 'IX_Attachment_VendorInvoiceNumber'
         AND object_id = OBJECT_ID('dbo.Attachment')
   )
BEGIN
    CREATE INDEX IX_Attachment_VendorInvoiceNumber
        ON dbo.[Attachment] ([VendorInvoiceNumber]);
END
GO
