-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: entities/address/sql/dbo.address.sql
-- Run manually in non-production environments.

EXEC CreateAddress
    @StreetOne = '123 Main St',
    @StreetTwo = 'Apt 1',
    @City = 'Anytown',
    @State = 'CA',
    @Zip = '12345',
    @Country = 'United States';
GO

EXEC ReadAddresses;
GO

EXEC ReadAddressById
    @Id = 1;
GO

EXEC ReadAddressByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadAddressByStreetOneAndCity
    @StreetOne = '123 Main St',
    @City = 'Anytown';
GO

EXEC ReadAddressByQboIdAndRealmId
    @QboId = 'PA-99',
    @RealmId = '9130353016965726';
GO

EXEC UpdateAddressById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @StreetOne = '123 Main St',
    @StreetTwo = 'Apt 1',
    @City = 'Anytown',
    @State = 'CA',
    @Zip = '12345',
    @Country = 'United States';
GO

EXEC SetAddressQboIdentity
    @Id = 2,
    @QboId = 'PA-99',
    @RealmId = '9130353016965726';
GO

EXEC DeleteAddressById
    @Id = 1;
GO
