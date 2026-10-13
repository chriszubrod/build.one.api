-- Migration: Add extraction fields to Attachment table
-- Run this script to add AI extraction support to existing Attachment table
--
-- Design: Extracted text is stored in Azure Blob Storage as JSON, not in SQL.
-- This keeps the database lean and leverages cheap blob storage for large text.

-- Add new columns for text extraction
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'ExtractionStatus'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD ExtractionStatus NVARCHAR(20) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'ExtractedTextBlobUrl'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD ExtractedTextBlobUrl NVARCHAR(MAX) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'ExtractionError'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD ExtractionError NVARCHAR(MAX) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'ExtractedDatetime'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD ExtractedDatetime DATETIME2(3) NULL;
END
GO

-- Create index on ExtractionStatus for efficient querying of pending extractions
IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Attachment_ExtractionStatus' AND object_id = OBJECT_ID('dbo.Attachment'))
BEGIN
CREATE INDEX IX_Attachment_ExtractionStatus ON [dbo].[Attachment] ([ExtractionStatus]);
END
GO

-- Procedure to update extraction status and results
CREATE OR ALTER PROCEDURE UpdateAttachmentExtraction
(
    @Id BIGINT,
    @ExtractionStatus NVARCHAR(20),
    @ExtractedTextBlobUrl NVARCHAR(MAX) = NULL,
    @ExtractionError NVARCHAR(MAX) = NULL,
    -- U-187: NULL preserves the existing VendorInvoiceNumber (so the cheap
    -- 'pending' re-mark from the QBO-attachable / receipt trigger never wipes a
    -- value); a non-NULL value overwrites. The Python service sets the
    -- freshly-parsed number directly -- it deliberately does NOT route through
    -- preserve_human_edited_ref (that helper is QBO-ref-specific). The SET clause's
    -- CASE-WHEN keeps the prior number on NULL, and also on any W-9-linked attachment.
    @VendorInvoiceNumber NVARCHAR(100) = NULL,
    -- U-535a: claim guard. When given, the write applies only while the row still
    -- carries this status and ModifiedDatetime (the token ClaimAttachmentsForExtraction
    -- returned). Otherwise it affects 0 rows and the caller reports claim_lost.
    @ExpectedStatus NVARCHAR(20) = NULL,
    @ExpectedModifiedDatetime DATETIME2(3) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Attachment]
    SET
        [ModifiedDatetime] = @Now,
        [ExtractionStatus] = @ExtractionStatus,
        [ExtractedTextBlobUrl] = @ExtractedTextBlobUrl,
        [ExtractionError] = @ExtractionError,
        [ExtractedDatetime] = CASE WHEN @ExtractionStatus = 'completed' THEN @Now ELSE [ExtractedDatetime] END,
        -- A W-9 link can be added while extraction runs, so the claim-time parse flag can be
        -- stale. The link is checked here, at write time, and wins over the passed number.
        -- The outer column is qualified: a bare [Id] would bind to TaxpayerAttachment.Id.
        [VendorInvoiceNumber] = CASE
            WHEN EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] AS t WHERE t.[AttachmentId] = dbo.[Attachment].[Id]) THEN [VendorInvoiceNumber]
            WHEN @VendorInvoiceNumber IS NOT NULL THEN @VendorInvoiceNumber
            ELSE [VendorInvoiceNumber]
        END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Filename],
        INSERTED.[OriginalFilename],
        INSERTED.[FileExtension],
        INSERTED.[ContentType],
        INSERTED.[FileSize],
        INSERTED.[FileHash],
        INSERTED.[BlobUrl],
        INSERTED.[Description],
        INSERTED.[Category],
        INSERTED.[Tags],
        INSERTED.[IsArchived],
        INSERTED.[Status],
        INSERTED.[DownloadCount],
        CONVERT(VARCHAR(19), INSERTED.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ExpirationDate], 120) AS [ExpirationDate],
        INSERTED.[StorageTier],
        INSERTED.[ExtractionStatus],
        INSERTED.[ExtractedTextBlobUrl],
        INSERTED.[ExtractionError],
        CONVERT(VARCHAR(19), INSERTED.[ExtractedDatetime], 120) AS [ExtractedDatetime],
        INSERTED.[VendorInvoiceNumber]
    WHERE [Id] = @Id
      AND (@ExpectedStatus IS NULL OR [ExtractionStatus] = @ExpectedStatus)
      AND (@ExpectedModifiedDatetime IS NULL OR [ModifiedDatetime] = @ExpectedModifiedDatetime);

    COMMIT TRANSACTION;
END;
GO

-- Procedure to read attachments pending extraction
CREATE OR ALTER PROCEDURE ReadAttachmentsPendingExtraction
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [ExtractionStatus] = 'pending' OR [ExtractionStatus] IS NULL
    ORDER BY [CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO

-- Atomically claims up to @Limit eligible PDFs (pending, or processing with an abandoned
-- lease) linked to an enabled group, marks them 'processing', and returns them. UPDLOCK +
-- READPAST keep two concurrent claims from taking the same row.
CREATE OR ALTER PROCEDURE ClaimAttachmentsForExtraction
(
    @Groups NVARCHAR(200) = NULL,
    @Limit INT = 1,
    @LeaseMinutes INT = 15
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Enabled TABLE ([Grp] NVARCHAR(20) NOT NULL PRIMARY KEY);

    INSERT INTO @Enabled ([Grp])
    SELECT DISTINCT LOWER(LTRIM(RTRIM([value])))
    FROM STRING_SPLIT(ISNULL(@Groups, ''), ',')
    WHERE LTRIM(RTRIM([value])) <> '';

    -- Candidates come from a non-locking snapshot. A locking scan with TOP and ORDER BY
    -- would U-lock every eligible row until commit, leaving a concurrent claim with nothing.
    DECLARE @Candidates TABLE ([Seq] INT NOT NULL PRIMARY KEY, [Id] BIGINT NOT NULL);

    INSERT INTO @Candidates ([Seq], [Id])
    SELECT ROW_NUMBER() OVER (ORDER BY a.[CreatedDatetime] ASC, a.[Id] ASC), a.[Id]
    FROM dbo.[Attachment] AS a
    WHERE a.[ExtractionStatus] IN ('pending', 'processing');

    DECLARE @Claimed TABLE ([Id] BIGINT NOT NULL PRIMARY KEY);
    DECLARE @Want INT = CASE WHEN @Limit > 0 THEN @Limit ELSE 0 END;
    DECLARE @Taken INT = 0;
    DECLARE @Seq INT = 0;
    DECLARE @Id BIGINT;

    WHILE @Taken < @Want
    BEGIN
        SET @Id = NULL;

        SELECT TOP (1) @Seq = c.[Seq], @Id = c.[Id]
        FROM @Candidates AS c
        WHERE c.[Seq] > @Seq
        ORDER BY c.[Seq];

        IF @Id IS NULL
            BREAK;

        -- READPAST skips a row another claim holds. The eligibility check runs on the locked,
        -- committed row, so a row claimed or finished since the snapshot drops out here.
        IF EXISTS (
            SELECT 1
            FROM dbo.[Attachment] AS a WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)
            WHERE a.[Id] = @Id
              AND (a.[ExtractionStatus] = 'pending'
                   OR (a.[ExtractionStatus] = 'processing'
                       AND a.[ModifiedDatetime] < DATEADD(MINUTE, -@LeaseMinutes, SYSUTCDATETIME())))
              AND (LOWER(a.[ContentType]) = 'application/pdf' OR LOWER(a.[FileExtension]) IN ('pdf', '.pdf'))
              AND (
                    (EXISTS (SELECT 1 FROM @Enabled WHERE [Grp] = 'bill')
                        AND EXISTS (SELECT 1 FROM dbo.[BillLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
                 OR (EXISTS (SELECT 1 FROM @Enabled WHERE [Grp] = 'bill_credit')
                        AND EXISTS (SELECT 1 FROM dbo.[BillCreditLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
                 OR (EXISTS (SELECT 1 FROM @Enabled WHERE [Grp] = 'expense')
                        AND EXISTS (SELECT 1 FROM dbo.[ExpenseLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
                 OR (EXISTS (SELECT 1 FROM @Enabled WHERE [Grp] = 'w9')
                        AND EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
                 OR (EXISTS (SELECT 1 FROM @Enabled WHERE [Grp] = 'coi')
                        AND EXISTS (SELECT 1 FROM dbo.[CertificateOfInsurance] AS x WHERE x.[AttachmentId] = a.[Id]))
                 OR (EXISTS (SELECT 1 FROM @Enabled WHERE [Grp] = 'license')
                        AND (EXISTS (SELECT 1 FROM dbo.[BusinessLicenseAttachment] AS x WHERE x.[AttachmentId] = a.[Id])
                             OR EXISTS (SELECT 1 FROM dbo.[ContractorsLicenseAttachment] AS x WHERE x.[AttachmentId] = a.[Id])))
              )
        )
        BEGIN
            UPDATE dbo.[Attachment]
            SET [ExtractionStatus] = 'processing',
                [ModifiedDatetime] = SYSUTCDATETIME()
            WHERE [Id] = @Id;

            INSERT INTO @Claimed ([Id]) VALUES (@Id);
            SET @Taken = @Taken + 1;
        END
    END;

    SELECT
        a.[Id],
        a.[PublicId],
        a.[RowVersion],
        CONVERT(VARCHAR(19), a.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), a.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        a.[Filename],
        a.[OriginalFilename],
        a.[FileExtension],
        a.[ContentType],
        a.[FileSize],
        a.[FileHash],
        a.[BlobUrl],
        a.[Description],
        a.[Category],
        a.[Tags],
        a.[IsArchived],
        a.[Status],
        a.[DownloadCount],
        CONVERT(VARCHAR(19), a.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), a.[ExpirationDate], 120) AS [ExpirationDate],
        a.[StorageTier],
        a.[ExtractionStatus],
        a.[ExtractedTextBlobUrl],
        a.[ExtractionError],
        CONVERT(VARCHAR(19), a.[ExtractedDatetime], 120) AS [ExtractedDatetime],
        a.[VendorInvoiceNumber],
        a.[ModifiedDatetime] AS [ClaimModifiedDatetime],
        CAST(CASE
            WHEN (EXISTS (SELECT 1 FROM dbo.[BillLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id])
               OR EXISTS (SELECT 1 FROM dbo.[BillCreditLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id])
               OR EXISTS (SELECT 1 FROM dbo.[ExpenseLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
             AND NOT EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] AS x WHERE x.[AttachmentId] = a.[Id])
            THEN 1 ELSE 0 END AS BIT) AS [ParseInvoiceNumber],
        CAST(CASE WHEN EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] AS x WHERE x.[AttachmentId] = a.[Id]) THEN 1 ELSE 0 END AS BIT) AS [IsTaxpayerDocument]
    FROM dbo.[Attachment] AS a
    INNER JOIN @Claimed AS k ON k.[Id] = a.[Id]
    ORDER BY a.[CreatedDatetime] ASC, a.[Id] ASC;
END;
GO

-- Group-scoped re-mark for the one-shot backfill. Each call processes at most @BatchSize
-- rows of one kind (failed when @IncludeFailed = 1, never-extracted when 0) and returns
-- `affected`. Completed rows are never selected for writing. @DryRun = 1 writes nothing.
CREATE OR ALTER PROCEDURE MarkAttachmentsPendingExtractionByGroup
(
    @Group NVARCHAR(20),
    @IncludeFailed BIT = 0,
    @BatchSize INT = 500,
    @DryRun BIT = 1
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Grp NVARCHAR(20) = LOWER(LTRIM(RTRIM(@Group)));

    IF @Grp IS NULL OR @Grp NOT IN ('bill', 'bill_credit', 'expense', 'w9', 'coi', 'license')
        THROW 50000, 'MarkAttachmentsPendingExtractionByGroup: unknown @Group (bill, bill_credit, expense, w9, coi, license)', 1;

    IF @BatchSize IS NULL OR @BatchSize < 1
        THROW 50000, 'MarkAttachmentsPendingExtractionByGroup: @BatchSize must be at least 1', 1;

    DECLARE @Members TABLE ([Id] BIGINT NOT NULL PRIMARY KEY);

    INSERT INTO @Members ([Id])
    SELECT a.[Id]
    FROM dbo.[Attachment] AS a
    WHERE (LOWER(a.[ContentType]) = 'application/pdf' OR LOWER(a.[FileExtension]) IN ('pdf', '.pdf'))
      AND (
            (@Grp = 'bill' AND EXISTS (SELECT 1 FROM dbo.[BillLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
         OR (@Grp = 'bill_credit' AND EXISTS (SELECT 1 FROM dbo.[BillCreditLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
         OR (@Grp = 'expense' AND EXISTS (SELECT 1 FROM dbo.[ExpenseLineItemAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
         OR (@Grp = 'w9' AND EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] AS x WHERE x.[AttachmentId] = a.[Id]))
         OR (@Grp = 'coi' AND EXISTS (SELECT 1 FROM dbo.[CertificateOfInsurance] AS x WHERE x.[AttachmentId] = a.[Id]))
         OR (@Grp = 'license' AND (
                EXISTS (SELECT 1 FROM dbo.[BusinessLicenseAttachment] AS x WHERE x.[AttachmentId] = a.[Id])
                OR EXISTS (SELECT 1 FROM dbo.[ContractorsLicenseAttachment] AS x WHERE x.[AttachmentId] = a.[Id])))
          );

    DECLARE @Affected INT = 0;

    IF @DryRun = 0
    BEGIN
        BEGIN TRANSACTION;

        UPDATE TOP (@BatchSize) a
        SET
            a.[ExtractionStatus] = 'pending',
            a.[ExtractionError] = CASE WHEN a.[ExtractionStatus] = 'failed' THEN NULL ELSE a.[ExtractionError] END,
            a.[ModifiedDatetime] = SYSUTCDATETIME()
        FROM dbo.[Attachment] AS a
        INNER JOIN @Members AS m ON m.[Id] = a.[Id]
        -- One kind per call: a shared TOP budget would let NULL rows starve failed ones.
        WHERE (@IncludeFailed = 1 AND a.[ExtractionStatus] = 'failed')
           OR (@IncludeFailed = 0 AND a.[ExtractionStatus] IS NULL);

        SET @Affected = @@ROWCOUNT;

        COMMIT TRANSACTION;
    END

    SELECT
        @Affected AS [affected],
        @Grp AS [group],
        COUNT(CASE WHEN a.[ExtractionStatus] IS NULL THEN 1 END) AS never_extracted_pdfs,
        COUNT(CASE WHEN a.[ExtractionStatus] = 'failed' THEN 1 END) AS failed_pdfs,
        COUNT(CASE WHEN a.[ExtractionStatus] = 'pending' THEN 1 END) AS already_pending,
        COUNT(CASE WHEN a.[ExtractionStatus] = 'completed' THEN 1 END) AS completed
    FROM @Members AS m
    INNER JOIN dbo.[Attachment] AS a ON a.[Id] = m.[Id];
END;
GO
