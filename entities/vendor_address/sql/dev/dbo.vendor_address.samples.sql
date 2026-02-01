-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/vendor_address/sql/dbo.vendor_address.sql
-- Run manually in non-production environments.

EXEC CreateVendorAddress
    @VendorId = 1,
    @AddressId = 1,
    @AddressTypeId = 1;
GO

EXEC ReadVendorAddresses;
GO

EXEC ReadVendorAddressById
    @Id = 1;
GO

EXEC ReadVendorAddressByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadVendorAddressByVendorId
    @VendorId = 1;
GO

EXEC ReadVendorAddressByAddressId
    @AddressId = 1;
GO

EXEC ReadVendorAddressByAddressTypeId
    @AddressTypeId = 1;
GO

EXEC UpdateVendorAddressById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @VendorId = 1,
    @AddressId = 1,
    @AddressTypeId = 1;
GO

EXEC DeleteVendorAddressById
    @Id = 1;
GO

EXEC DeleteVendorAddressByVendorId
    @VendorId = 1;
GO
