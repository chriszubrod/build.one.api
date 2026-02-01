-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/intuit/qbo/company_info/sql/qbo.company_info.sql
-- Run manually in non-production environments.

EXEC CreateQboCompanyInfo
    @QboId = '1',
    @SyncToken = '0',
    @RealmId = '123456789',
    @CompanyName = 'Test Company',
    @LegalName = 'Test Company Legal',
    @CompanyAddrId = NULL,
    @LegalAddrId = NULL,
    @CustomerCommunicationAddrId = NULL,
    @TaxPayerId = '12-3456789',
    @FiscalYearStartMonth = 1,
    @Country = 'USA',
    @Email = 'test@example.com',
    @WebAddr = 'https://example.com',
    @CurrencyRef = 'USD';
GO

EXEC ReadQboCompanyInfos;
GO

EXEC ReadQboCompanyInfoByQboId
    @QboId = '1';
GO

EXEC ReadQboCompanyInfoById
    @Id = 1;
GO

EXEC ReadQboCompanyInfoByRealmId
    @RealmId = '123456789';
GO

EXEC UpdateQboCompanyInfoByQboId
    @QboId = '1',
    @RowVersion = 0x0000000000020B74,
    @SyncToken = '1',
    @RealmId = '123456789',
    @CompanyName = 'Test Company Updated',
    @LegalName = 'Test Company Legal Updated',
    @CompanyAddrId = NULL,
    @LegalAddrId = NULL,
    @CustomerCommunicationAddrId = NULL,
    @TaxPayerId = '12-3456789',
    @FiscalYearStartMonth = 1,
    @Country = 'USA',
    @Email = 'test@example.com',
    @WebAddr = 'https://example.com',
    @CurrencyRef = 'USD';
GO

EXEC DeleteQboCompanyInfoByQboId
    @QboId = '1';
GO
