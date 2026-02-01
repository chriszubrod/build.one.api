-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql
-- Run manually in non-production environments.

SELECT * FROM dbo.BillLineItemAttachment;

EXEC CreateBillLineItemAttachment
    @BillLineItemId = 1,
    @AttachmentId = 1;
GO

EXEC ReadBillLineItemAttachments;
GO

EXEC ReadBillLineItemAttachmentById
    @Id = 1;
GO

EXEC ReadBillLineItemAttachmentByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadBillLineItemAttachmentByBillLineItemId
    @BillLineItemId = 1;
GO

EXEC DeleteBillLineItemAttachmentById
    @Id = 2;
GO
