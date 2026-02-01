-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/attachment/sql/dbo.attachment.sql
-- Run manually in non-production environments.

EXEC CreateAttachment
    @Filename = 'test.pdf',
    @OriginalFilename = 'test.pdf',
    @FileExtension = 'pdf',
    @ContentType = 'application/pdf',
    @FileSize = 1024,
    @FileHash = NULL,
    @BlobUrl = 'https://example.blob.core.windows.net/attachments/test.pdf',
    @Description = 'Test attachment',
    @Category = 'Invoice',
    @Tags = NULL,
    @IsArchived = 0,
    @Status = 'Draft',
    @ExpirationDate = NULL,
    @StorageTier = 'Hot';
GO

EXEC ReadAttachments;
GO

EXEC ReadAttachmentById
    @Id = 1;
GO

EXEC ReadAttachmentByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadAttachmentByCategory
    @Category = 'Invoice';
GO

EXEC ReadAttachmentByHash
    @FileHash = 'abc123';
GO

EXEC UpdateAttachmentById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @Filename = 'test_updated.pdf',
    @OriginalFilename = 'test.pdf',
    @FileExtension = 'pdf',
    @ContentType = 'application/pdf',
    @FileSize = 2048,
    @FileHash = NULL,
    @BlobUrl = 'https://example.blob.core.windows.net/attachments/test.pdf',
    @Description = 'Updated test attachment',
    @Category = 'Invoice',
    @Tags = NULL,
    @IsArchived = 0,
    @Status = 'Approved',
    @ExpirationDate = NULL,
    @StorageTier = 'Hot';
GO

EXEC DeleteAttachmentById
    @Id = 1;
GO

EXEC IncrementDownloadCount
    @Id = 1;
GO
