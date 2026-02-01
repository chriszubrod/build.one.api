-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/intuit/qbo/client/sql/qbo.client.sql
-- Run manually in non-production environments.

EXEC CreateQboClient
    @App = 'build.one',
    @ClientId = 'ABxFNbNXlqWDlNAa7tOakJ5ib9RNpgBSU5qIIxLf1PCAQztL0i',
    @ClientSecret = 'JZKkGDmKqPyAs7TlmAFIR2ih8DIylq9tlgzI4phm';


DROP PROCEDURE IF EXISTS ReadQboClients;
GO

EXEC ReadQboClients;


DROP PROCEDURE IF EXISTS ReadQboClientByApp;
GO

EXEC ReadQboClientByApp @App = 'build.one';


DROP PROCEDURE IF EXISTS ReadQboClientByClientId;
GO

EXEC ReadQboClientByClientId @ClientId = 'test-client';


DROP PROCEDURE IF EXISTS UpdateQboClientByApp;
GO

EXEC UpdateQboClientByApp
    @App = 'build.one',
    @ClientId = 'client-id',
    @ClientSecret = 'client-secret';


DROP PROCEDURE IF EXISTS UpdateQboClientByClientId;
GO

EXEC UpdateQboClientByClientId
    @App = 'build.one',
    @ClientId = 'updated-client',
    @ClientSecret = 'updated-secret';


DROP PROCEDURE IF EXISTS DeleteQboClientByApp;
GO

EXEC DeleteQboClientByApp
