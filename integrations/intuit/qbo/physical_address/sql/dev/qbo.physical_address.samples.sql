-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/intuit/qbo/physical_address/sql/qbo.physical_address.sql
-- Run manually in non-production environments.

EXEC CreateQboPhysicalAddress
    @QboId = '1',
    @Line1 = '123 Main St',
    @Line2 = 'Suite 100',
    @City = 'San Francisco',
    @Country = 'USA',
    @CountrySubDivisionCode = 'CA',
    @PostalCode = '94105';
GO

EXEC ReadQboPhysicalAddresses;
GO

EXEC ReadQboPhysicalAddressById @Id = '1';



DROP PROCEDURE IF EXISTS ReadQboPhysicalAddressByPublicId;
GO

EXEC ReadQboPhysicalAddressByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadQboPhysicalAddressByQboId
    @QboId = '1';
GO

EXEC UpdateQboPhysicalAddressById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @QboId = '1',
    @Line1 = '123 Main St',
    @Line2 = 'Suite 100',
    @City = 'San Francisco',
    @Country = 'USA',
    @CountrySubDivisionCode = 'CA',
    @PostalCode = '94105';
GO

EXEC DeleteQboPhysicalAddressById
    @Id = 1;
GO
