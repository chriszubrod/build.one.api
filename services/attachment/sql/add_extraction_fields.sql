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
GO

CREATE OR ALTER PROCEDURE UpdateAttachmentExtraction
(
    @Id BIGINT,
    @ExtractionStatus NVARCHAR(20),
    @ExtractedTextBlobUrl NVARCHAR(MAX) = NULL,
    @ExtractionError NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Attachment]
    SET
        [ModifiedDatetime] = @Now,
        [ExtractionStatus] = @ExtractionStatus,
        [ExtractedTextBlobUrl] = @ExtractedTextBlobUrl,
        [ExtractionError] = @ExtractionError,
        [ExtractedDatetime] = CASE WHEN @ExtractionStatus = 'completed' THEN @Now ELSE [ExtractedDatetime] END
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
        CONVERT(VARCHAR(19), INSERTED.[ExtractedDatetime], 120) AS [ExtractedDatetime]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- Procedure to read attachments pending extraction
GO

CREATE OR ALTER PROCEDURE ReadAttachmentsPendingExtraction
AS
BEGIN
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
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime]
    FROM dbo.[Attachment]
    WHERE [ExtractionStatus] = 'pending' OR [ExtractionStatus] IS NULL
    ORDER BY [CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO
