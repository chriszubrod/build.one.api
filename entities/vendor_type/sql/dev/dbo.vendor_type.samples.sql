-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/vendor_type/sql/dbo.vendor_type.sql
-- Run manually in non-production environments.

EXEC CreateVendorType
    @Name = 'Tradesman',
    @Description = 'Sub contractor for a specific trade.';
GO

EXEC CreateVendorType
    @Name = 'Materials Supplier',
    @Description = 'Vendor that supplies construction materials.';
GO

EXEC ReadVendorTypes;
GO

EXEC ReadVendorTypeById
    @Id = 1;
GO

EXEC ReadVendorTypeByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadVendorTypeByName
    @Name = 'Tradesman';
GO

EXEC UpdateVendorTypeById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @Name = 'Updated Vendor',
    @Description = 'Updated vendor type description';
GO

EXEC DeleteVendorTypeById
    @Id = 1;
GO
