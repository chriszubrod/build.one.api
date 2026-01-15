IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ms')
    EXEC('CREATE SCHEMA ms AUTHORIZATION dbo;');
GO

IF OBJECT_ID('ms.Client', 'U') IS NOT NULL
    DROP TABLE ms.Client;
GO

CREATE TABLE ms.Client
(
    App NVARCHAR(MAX) NOT NULL,
    ClientId NVARCHAR(MAX) NOT NULL,
    ClientSecret NVARCHAR(MAX) NOT NULL,
    TenantId NVARCHAR(MAX) NOT NULL,
    RedirectUri NVARCHAR(MAX) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateMsClient;
GO

CREATE PROCEDURE CreateMsClient
(
    @App NVARCHAR(MAX),
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX),
    @TenantId NVARCHAR(MAX),
    @RedirectUri NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    INSERT INTO ms.Client (App, ClientId, ClientSecret, TenantId, RedirectUri)
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret,
        INSERTED.TenantId,
        INSERTED.RedirectUri
    VALUES (@App, @ClientId, @ClientSecret, @TenantId, @RedirectUri);
    COMMIT TRANSACTION;
END;
GO

EXEC CreateMsClient
    @App = 'buildone',
    @ClientId = '8bb81ea6-2887-49ad-ba40-4b77e343b975',
    @ClientSecret = 'aXa8Q~wH_V8mBpTcdG4un~4XG1UhfiF4FdiWucxR',
    @TenantId = '5daf13a1-5113-4d2c-bb43-13c6d113cf18',
    @RedirectUri = 'http://localhost:8000/api/v1/ms/auth/request/callback';

UPDATE ms.Client SET TenantId = '5daf13a1-5113-4d2c-bb43-13c6d113cf18' WHERE App = 'buildone';

DROP PROCEDURE IF EXISTS ReadMsClients;
GO

CREATE PROCEDURE ReadMsClients
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        App,
        ClientId,
        ClientSecret,
        TenantId,
        RedirectUri
    FROM ms.Client;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsClients;


DROP PROCEDURE IF EXISTS ReadMsClientByApp;
GO

CREATE PROCEDURE ReadMsClientByApp
(
    @App NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        App,
        ClientId,
        ClientSecret,
        TenantId,
        RedirectUri
    FROM ms.Client
    WHERE App = @App;
    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsClientByApp @App = 'build.one';


DROP PROCEDURE IF EXISTS ReadMsClientByClientId;
GO

CREATE PROCEDURE ReadMsClientByClientId
(
    @ClientId NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        App,
        ClientId,
        ClientSecret,
        TenantId,
        RedirectUri
    FROM ms.Client
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsClientByClientId @ClientId = 'test-client-id';


DROP PROCEDURE IF EXISTS UpdateMsClientByApp;
GO

CREATE PROCEDURE UpdateMsClientByApp
(
    @App NVARCHAR(MAX),
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX),
    @TenantId NVARCHAR(MAX),
    @RedirectUri NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    UPDATE ms.Client
    SET App = @App,
        ClientId = @ClientId,
        ClientSecret = @ClientSecret,
        TenantId = @TenantId,
        RedirectUri = @RedirectUri
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret,
        INSERTED.TenantId,
        INSERTED.RedirectUri
    WHERE App = @App;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateMsClientByApp
    @App = 'build.one',
    @ClientId = 'updated-client-id',
    @ClientSecret = 'updated-client-secret',
    @TenantId = 'updated-tenant-id',
    @RedirectUri = 'https://updated-domain.com/api/v1/ms/auth/request/callback';


DROP PROCEDURE IF EXISTS UpdateMsClientByClientId;
GO

CREATE PROCEDURE UpdateMsClientByClientId
(
    @App NVARCHAR(MAX),
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX),
    @TenantId NVARCHAR(MAX),
    @RedirectUri NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    UPDATE ms.Client
    SET App = @App,
        ClientId = @ClientId,
        ClientSecret = @ClientSecret,
        TenantId = @TenantId,
        RedirectUri = @RedirectUri
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret,
        INSERTED.TenantId,
        INSERTED.RedirectUri
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateMsClientByClientId
    @App = 'build.one',
    @ClientId = 'updated-client-id',
    @ClientSecret = 'updated-client-secret',
    @TenantId = 'updated-tenant-id',
    @RedirectUri = 'https://updated-domain.com/api/v1/ms/auth/request/callback';


DROP PROCEDURE IF EXISTS DeleteMsClientByApp;
GO

CREATE PROCEDURE DeleteMsClientByApp
(
    @App NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM ms.Client
    OUTPUT
        DELETED.App,
        DELETED.ClientId,
        DELETED.ClientSecret,
        DELETED.TenantId,
        DELETED.RedirectUri
    WHERE App = @App;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteMsClientByApp
    @App = 'buildone';
