-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/taxpayer/sql/dbo.taxpayer.sql
-- Run manually in non-production environments.

EXEC CreateTaxpayer
    @EntityName = 'Acme Supply Co.',
    @BusinessName = 'Acme Supply Co.',
    @Classification = 'Corporation',
    @TaxpayerIdNumber = '1234567890';
GO

EXEC ReadTaxpayers;
GO

EXEC ReadTaxpayerById
    @Id = 1;
GO

EXEC ReadTaxpayerByPublicId
    @PublicId = 1;
GO

EXEC ReadTaxpayerByName
    @EntityName = 'Acme Supply Co.';
GO

EXEC ReadTaxpayerByBusinessName
    @BusinessName = 'Acme Supply Co.';
GO

EXEC ReadTaxpayerByTaxpayerIdNumber
    @TaxpayerIdNumber = '1234567890';
GO

EXEC UpdateTaxpayerById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @EntityName = 'Acme Supply Co. Updated',
    @BusinessName = 'Acme Supply Co. Updated',
    @Classification = 'Corporation',
    @TaxpayerIdNumber = '1234567890';
GO

EXEC DeleteTaxpayerById
    @Id = 1;
GO
