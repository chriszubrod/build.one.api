-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/role_module/sql/dbo.rolemodule.sql
-- Run manually in non-production environments.

EXEC CreateRoleModule
    @RoleId = '00000000-0000-0000-0000-000000000000',
    @ModuleId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadRoleModules;
GO

EXEC ReadRoleModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadRoleModuleByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadRoleModuleByRoleId
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadRoleModuleByModuleId
    @ModuleId = '00000000-0000-0000-0000-000000000000';
GO

EXEC UpdateRoleModuleById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @RoleId = '00000000-0000-0000-0000-000000000000',
    @ModuleId = '00000000-0000-0000-0000-000000000000';
GO

EXEC DeleteRoleModuleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
