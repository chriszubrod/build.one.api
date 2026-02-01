-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/user_role/sql/dbo.userrole.sql
-- Run manually in non-production environments.

EXEC CreateUserRole
    @UserId = '00000000-0000-0000-0000-000000000000',
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadUserRoles;
GO

EXEC ReadUserRoleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadUserRoleByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadUserRoleByUserId
    @UserId = '0000000';
GO

EXEC ReadUserRoleByRoleId
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO

EXEC UpdateUserRoleById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @UserId = '00000000-0000-0000-0000-000000000000',
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO

EXEC DeleteUserRoleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
