-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/ms/client/sql/ms.client.sql
-- Run manually in non-production environments.

EXEC CreateMsClient
    @App = 'buildone',
    @ClientId = '8bb81ea6-2887-49ad-ba40-4b77e343b975',
    @ClientSecret = 'aXa8Q~wH_V8mBpTcdG4un~4XG1UhfiF4FdiWucxR',
    @TenantId = '5daf13a1-5113-4d2c-bb43-13c6d113cf18',
    @RedirectUri = 'http://localhost:8000/api/v1/ms/auth/request/callback';

UPDATE ms.Client SET TenantId = '5daf13a1-5113-4d2c-bb43-13c6d113cf18' WHERE App = 'buildone';

DROP PROCEDURE IF EXISTS ReadMsClients;
GO

EXEC ReadMsClients;


DROP PROCEDURE IF EXISTS ReadMsClientByApp;
GO

EXEC ReadMsClientByApp @App = 'build.one';


DROP PROCEDURE IF EXISTS ReadMsClientByClientId;
GO

EXEC ReadMsClientByClientId @ClientId = 'test-client-id';


DROP PROCEDURE IF EXISTS UpdateMsClientByApp;
GO

EXEC UpdateMsClientByApp
    @App = 'build.one',
    @ClientId = 'updated-client-id',
    @ClientSecret = 'updated-client-secret',
    @TenantId = 'updated-tenant-id',
    @RedirectUri = 'https://updated-domain.com/api/v1/ms/auth/request/callback';


DROP PROCEDURE IF EXISTS UpdateMsClientByClientId;
GO

EXEC UpdateMsClientByClientId
    @App = 'build.one',
    @ClientId = 'updated-client-id',
    @ClientSecret = 'updated-client-secret',
    @TenantId = 'updated-tenant-id',
    @RedirectUri = 'https://updated-domain.com/api/v1/ms/auth/request/callback';


DROP PROCEDURE IF EXISTS DeleteMsClientByApp;
GO

EXEC DeleteMsClientByApp
