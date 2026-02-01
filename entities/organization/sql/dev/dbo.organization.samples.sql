-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/organization/sql/dbo.organization.sql
-- Run manually in non-production environments.

EXEC CreateOrganization
    @Name = 'B. Christopher & Co., LLC',
    @Website = 'https://wwww.bcand.company';
GO

EXEC ReadOrganizations;
GO

EXEC ReadOrganizationById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadOrganizationByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadOrganizationByName
    @Name = 'B. Christopher & Co., LLC';
GO

EXEC UpdateOrganizationById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = '0x0000000000000000',
    @Name = 'BuildOne',
    @Website = 'https://buildone.com';
GO

EXEC DeleteOrganizationById
    @Id = '00000000-0000-0000-0000-000000000000'
GO
