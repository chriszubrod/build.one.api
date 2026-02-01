-- Migration: Add AI Categorization fields to Attachment table
-- Phase 6: Auto-Categorization

-- Add categorization columns if they don't exist
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'AICategory'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD AICategory NVARCHAR(50) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'AICategoryConfidence'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD AICategoryConfidence DECIMAL(5,4) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'AICategoryStatus'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD AICategoryStatus NVARCHAR(20) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'AICategoryReasoning'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD AICategoryReasoning NVARCHAR(500) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'AIExtractedFields'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD AIExtractedFields NVARCHAR(MAX) NULL;  -- JSON storage for extracted fields
END
GO

IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'Attachment' AND COLUMN_NAME = 'CategorizedDatetime'
)
BEGIN
    ALTER TABLE dbo.Attachment
    ADD CategorizedDatetime DATETIME2 NULL;
END
GO

-- Create index on AICategory for filtering
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes 
    WHERE name = 'IX_Attachment_AICategory' AND object_id = OBJECT_ID('dbo.Attachment')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_Attachment_AICategory
    ON dbo.Attachment (AICategory)
    WHERE AICategory IS NOT NULL;
END
GO

-- Create index on AICategoryStatus for finding pending categorizations
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes 
    WHERE name = 'IX_Attachment_AICategoryStatus' AND object_id = OBJECT_ID('dbo.Attachment')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_Attachment_AICategoryStatus
    ON dbo.Attachment (AICategoryStatus)
    WHERE AICategoryStatus IS NOT NULL;
END
GO

-- Stored procedure to update categorization
CREATE OR ALTER PROCEDURE dbo.UpdateAttachmentCategorization
    @Id INT,
    @AICategory NVARCHAR(50),
    @AICategoryConfidence DECIMAL(5,4),
    @AICategoryStatus NVARCHAR(20),
    @AICategoryReasoning NVARCHAR(500) = NULL,
    @AIExtractedFields NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE dbo.Attachment
    SET 
        AICategory = @AICategory,
        AICategoryConfidence = @AICategoryConfidence,
        AICategoryStatus = @AICategoryStatus,
        AICategoryReasoning = @AICategoryReasoning,
        AIExtractedFields = @AIExtractedFields,
        CategorizedDatetime = GETUTCDATE(),
        ModifiedDatetime = GETUTCDATE()
    WHERE Id = @Id;
    
    SELECT @@ROWCOUNT AS RowsAffected;
END
GO

-- Stored procedure to read attachments pending categorization
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentsPendingCategorization
    @Limit INT = 50
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT TOP (@Limit)
        Id,
        PublicId,
        RowVersion,
        CreatedDatetime,
        ModifiedDatetime,
        Filename,
        OriginalFilename,
        FileExtension,
        ContentType,
        FileSize,
        FileHash,
        BlobUrl,
        Description,
        Category,
        Tags,
        IsArchived,
        Status,
        DownloadCount,
        LastDownloadedDatetime,
        ExpirationDate,
        StorageTier,
        ExtractionStatus,
        ExtractedTextBlobUrl,
        ExtractionError,
        ExtractedDatetime,
        AICategory,
        AICategoryConfidence,
        AICategoryStatus,
        AICategoryReasoning,
        AIExtractedFields,
        CategorizedDatetime
    FROM dbo.Attachment
    WHERE ExtractionStatus = 'completed'
      AND (AICategory IS NULL OR AICategoryStatus = 'pending')
      AND IsArchived = 0
    ORDER BY CreatedDatetime DESC;
END
GO

-- Stored procedure to confirm or reject AI categorization
CREATE OR ALTER PROCEDURE dbo.ConfirmAttachmentCategorization
    @Id INT,
    @Confirmed BIT,
    @ManualCategory NVARCHAR(50) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    IF @Confirmed = 1
    BEGIN
        -- User confirmed AI suggestion
        UPDATE dbo.Attachment
        SET 
            AICategoryStatus = 'confirmed',
            ModifiedDatetime = GETUTCDATE()
        WHERE Id = @Id;
    END
    ELSE
    BEGIN
        -- User rejected AI suggestion, apply manual category
        UPDATE dbo.Attachment
        SET 
            AICategory = ISNULL(@ManualCategory, AICategory),
            AICategoryStatus = 'manual',
            AICategoryConfidence = CASE WHEN @ManualCategory IS NOT NULL THEN 1.0 ELSE AICategoryConfidence END,
            AICategoryReasoning = CASE WHEN @ManualCategory IS NOT NULL THEN 'Manually assigned by user' ELSE AICategoryReasoning END,
            ModifiedDatetime = GETUTCDATE()
        WHERE Id = @Id;
    END
    
    SELECT @@ROWCOUNT AS RowsAffected;
END
GO

PRINT 'Categorization fields and procedures created successfully.';
