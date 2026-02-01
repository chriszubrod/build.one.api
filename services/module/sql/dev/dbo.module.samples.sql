-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/module/sql/dbo.module.sql
-- Run manually in non-production environments.

EXEC ReadModules;
GO

EXEC ReadModules;
GO

EXEC ReadModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadModuleByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadModuleByName
    @Name = 'Module Name';
GO

EXEC UpdateModuleById
    @Id = 4,
    @RowVersion = 0x0000000000021B33,
    @Name = 'Organizations',
    @Route = '/organization/list';
GO

EXEC DeleteModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
