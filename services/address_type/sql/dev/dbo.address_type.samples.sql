-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/address_type/sql/dbo.address_type.sql
-- Run manually in non-production environments.

EXEC ReadAddressTypes;


DROP PROCEDURE IF EXISTS DeleteAddressTypeById;
GO

EXEC CreateAddressType
    @Name = 'Shipping',
    @Description = 'Shipping address',
    @DisplayOrder = 3;
GO

EXEC ReadAddressTypes;
GO

EXEC ReadAddressTypeById
    @Id = 1;
GO

EXEC ReadAddressTypeByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadAddressTypeName
    @Name = 'Mailing';
GO

EXEC UpdateAddressTypeById
    @Id = 3,
    @RowVersion = 0x0000000000021B56,
    @Name = 'Mailing',
    @Description = 'Mailing address',
    @DisplayOrder = 3;
GO

EXEC DeleteAddressTypeById
    @Id = 1;
GO
