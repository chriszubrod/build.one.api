-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/ms/auth/sql/ms.auth.sql
-- Run manually in non-production environments.

EXEC CreateMsAuth
    @Code = 'test-code',
    @State = 'test-state',
    @TokenType = 'Bearer',
    @AccessToken = 'test-access-token',
    @ExpiresIn = 3600,
    @RefreshToken = 'test-refresh-token',
    @Scope = 'Mail.Read Mail.Send Sites.ReadWrite User.Read offline_access',
    @TenantId = 'test-tenant-id',
    @UserId = 'test-user-id';


DROP PROCEDURE IF EXISTS ReadMsAuths;
GO

EXEC ReadMsAuths;


DROP PROCEDURE IF EXISTS ReadMsAuthById;
GO

EXEC ReadMsAuthById
    @Id = 1;
GO

EXEC ReadMsAuthByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadMsAuthByTenantId @TenantId = 'test-tenant-id';


DROP PROCEDURE IF EXISTS UpdateMsAuthByTenantId;
GO

EXEC UpdateMsAuthByTenantId
    @Code = 'test-code',
    @State = 'test-state',
    @TokenType = 'Bearer',
    @AccessToken = 'test-access-token',
    @ExpiresIn = 3600,
    @RefreshToken = 'test-refresh-token',
    @Scope = 'Mail.Read Mail.Send Sites.ReadWrite User.Read offline_access',
    @TenantId = 'test-tenant-id',
    @UserId = 'test-user-id';


DROP PROCEDURE IF EXISTS DeleteMsAuthByTenantId;
GO

EXEC DeleteMsAuthByTenantId
