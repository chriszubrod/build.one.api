-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/intuit/qbo/company_info/connector/sql/qbo.company_info.company.sql
-- Run manually in non-production environments.

EXEC CreateCompanyInfoCompany
    @CompanyId = 1,
    @QboCompanyInfoId = 1;
GO

EXEC ReadCompanyInfoCompanyById
    @Id = 1;
GO

EXEC ReadCompanyInfoCompanyByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadCompanyInfoCompanyByCompanyId
    @CompanyId = 1;
GO

EXEC ReadCompanyInfoCompanyByQboCompanyInfoId
    @QboCompanyInfoId = 1;
GO

EXEC UpdateCompanyInfoCompanyById
    @Id = 1,
    @RowVersion = 0x0000000000021CD7,
    @CompanyId = 1,
    @QboCompanyInfoId = 1;
GO

EXEC DeleteCompanyInfoCompanyById
    @Id = 1;
GO

SELECT * FROM [qbo].[CompanyInfoCompany];
