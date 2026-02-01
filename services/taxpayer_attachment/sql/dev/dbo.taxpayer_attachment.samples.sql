-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/taxpayer_attachment/sql/dbo.taxpayer_attachment.sql
-- Run manually in non-production environments.

EXEC CreateTaxpayerAttachment
    @TaxpayerId = 1,
    @AttachmentId = 1;
GO

EXEC ReadTaxpayerAttachments;
GO

EXEC ReadTaxpayerAttachmentById
    @Id = 1;
GO

EXEC ReadTaxpayerAttachmentByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadTaxpayerAttachmentsByTaxpayerId
    @TaxpayerId = 1;
GO

EXEC DeleteTaxpayerAttachmentById
    @Id = 2;
GO
