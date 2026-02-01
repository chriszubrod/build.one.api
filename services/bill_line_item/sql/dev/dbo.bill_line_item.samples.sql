-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/bill_line_item/sql/dbo.bill_line_item.sql
-- Run manually in non-production environments.

SELECT * FROM dbo.BillLineItem;

EXEC CreateBillLineItem
    @BillId = 1,
    @SubCostCodeId = NULL,
    @Description = 'Sample bill line item',
    @Quantity = 10,
    @Rate = 50.00,
    @IsBillable = 1,
    @IsBilled = 0,
    @Markup = 0.10,
    @IsDraft = 0;
GO

EXEC ReadBillLineItems;
GO

EXEC ReadBillLineItemById
    @Id = 1;
GO

EXEC ReadBillLineItemByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadBillLineItemsByBillId
    @BillId = 1;
GO

EXEC UpdateBillLineItemById
    @Id = 1,
    @RowVersion = 0x0000000000020B74,
    @BillId = 1,
    @SubCostCodeId = NULL,
    @Description = 'Updated bill line item',
    @Quantity = 15,
    @Rate = 60.00,
    @IsBillable = 1,
    @IsBilled = 0,
    @Markup = 0.15;
GO

EXEC DeleteBillLineItemById
    @Id = 1;
GO
