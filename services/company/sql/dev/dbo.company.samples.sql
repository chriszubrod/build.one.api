-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/company/sql/dbo.company.sql
-- Run manually in non-production environments.

EXEC CreateCompany
    @Name = 'Rogers Build, Inc.',
    @Website = 'https://www.rogersbuild.com';
GO

EXEC ReadCompanies;
GO

EXEC ReadCompanyById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadCompanyByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadCompanyByName
    @Name = 'BuildOne';
GO

EXEC UpdateCompanyById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @Name = 'BuildOne',
    @Website = 'https://buildone.com';
GO

EXEC DeleteCompanyById
    @Id = 3;
GO
