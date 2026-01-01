IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'buildone')
    EXEC('CREATE SCHEMA qbo AUTHORIZATION dbo;');
GO




IF OBJECT_ID('qbo.Client', 'U') IS NOT NULL
    DROP TABLE qbo.Client;
GO



CREATE TABLE qbo.Client
(
    App NVARCHAR(MAX) NOT NULL,
    ClientId NVARCHAR(MAX) NOT NULL,
    ClientSecret NVARCHAR(MAX) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateQboClient;
GO

CREATE PROCEDURE CreateQboClient
(
    @App NVARCHAR(MAX),
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    INSERT INTO qbo.Client (App, ClientId, ClientSecret)
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret
    VALUES (@App, @ClientId, @ClientSecret);
    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboClient
    @App = 'build.one',
    @ClientId = 'ABxFNbNXlqWDlNAa7tOakJ5ib9RNpgBSU5qIIxLf1PCAQztL0i',
    @ClientSecret = 'JZKkGDmKqPyAs7TlmAFIR2ih8DIylq9tlgzI4phm';


DROP PROCEDURE IF EXISTS ReadQboClients;
GO

CREATE PROCEDURE ReadQboClients
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        App,
        ClientId,
        ClientSecret
    FROM qbo.Client;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboClients;


DROP PROCEDURE IF EXISTS ReadQboClientByApp;
GO

CREATE PROCEDURE ReadQboClientByApp
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
        ClientSecret
    FROM qbo.Client
    WHERE App = @App;
    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboClientByApp @App = 'build.one';


DROP PROCEDURE IF EXISTS ReadQboClientByClientId;
GO

CREATE PROCEDURE ReadQboClientByClientId
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
        ClientSecret
    FROM qbo.Client
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboClientByClientId @ClientId = 'test-client';


DROP PROCEDURE IF EXISTS UpdateQboClientByApp;
GO

CREATE PROCEDURE UpdateQboClientByApp
(
    @App NVARCHAR(MAX),
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    UPDATE qbo.Client
    SET App = @App,
        ClientId = @ClientId,
        ClientSecret = @ClientSecret
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret
    WHERE App = @App;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateQboClientByApp
    @App = 'build.one',
    @ClientId = 'updated-client',
    @ClientSecret = 'updated-secret';


DROP PROCEDURE IF EXISTS UpdateQboClientByClientId;
GO

CREATE PROCEDURE UpdateQboClientByClientId
(
    @App NVARCHAR(MAX),
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    UPDATE qbo.Client
    SET App = @App,
        ClientId = @ClientId,
        ClientSecret = @ClientSecret
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateQboClientByClientId
    @App = 'build.one',
    @ClientId = 'updated-client',
    @ClientSecret = 'updated-secret';


DROP PROCEDURE IF EXISTS DeleteQboClientByApp;
GO

CREATE PROCEDURE DeleteQboClientByApp
(
    @App NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM qbo.Client
    OUTPUT
        DELETED.App,
        DELETED.ClientId,
        DELETED.ClientSecret
    WHERE App = @App;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteQboClientByApp
    @App = 'build.one';
