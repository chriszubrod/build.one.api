-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/integration/sql/dbo.integration.sql
-- Run manually in non-production environments.

EXEC CreateIntegration
    @Name = 'QuickBooks Online',
    @Status = 'disconnected';
GO

EXEC ReadIntegrations;
GO

EXEC ReadIntegrationById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadIntegrationByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadIntegrationByName
    @Name = 'QuickBooks Online';
GO

EXEC UpdateIntegrationById
    @Id = 1,
    @RowVersion = 0x0000000000021B43,
    @Name = 'QuickBooks Online',
    @Status = 'disconnected';
GO

EXEC DeleteIntegrationById
    @Id = 2;
GO
