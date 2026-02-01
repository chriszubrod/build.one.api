-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/vendor/sql/dbo.vendor.sql
-- Run manually in non-production environments.

EXEC CreateVendor
    @Name = 'Acme Supply Co.',
    @Abbreviation = 'ACME',
    @VendorTypeId = 1,
    @TaxpayerId = 1;
GO

EXEC ReadVendors;
GO

EXEC ReadVendorById
    @Id = 1;
GO

EXEC ReadVendorByPublicId
    @PublicId = 1;
GO

EXEC ReadVendorByName
    @Name = 'Acme Supply Co.';
GO

EXEC UpdateVendorById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @Name = 'Acme Supply Co. Updated',
    @Abbreviation = 'ACME-UPD',
    @VendorTypeId = 1,
    @TaxpayerId = 1;
GO

EXEC DeleteVendorById
    @Id = 1;
GO
