IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'buildone')
    EXEC('CREATE SCHEMA qbo AUTHORIZATION dbo;');
GO




IF OBJECT_ID('qbo.Client', 'U') IS NOT NULL
    DROP TABLE qbo.Client;
GO



CREATE TABLE qbo.Client
(
    ClientId NVARCHAR(MAX) NOT NULL,
    ClientSecret NVARCHAR(MAX) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateQboClient;
GO

CREATE PROCEDURE CreateQboClient
(
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    INSERT INTO qbo.Client (ClientId, ClientSecret)
    OUTPUT
        INSERTED.ClientId,
        INSERTED.ClientSecret
    VALUES (@ClientId, @ClientSecret);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboClient @ClientId = 'ABA5fzHtGjWvIqMOQs8qKq12Lg0U23bRCE42Yc2YKvpZy5XeP6', @ClientSecret = 'trXfM3uX9L7MoIGOQ73bfKhArEeS6dEj0W9vpbkG';


DROP PROCEDURE IF EXISTS ReadQboClients;
GO

CREATE PROCEDURE ReadQboClients
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        ClientId,
        ClientSecret
    FROM qbo.Client;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboClients;


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
        ClientId,
        ClientSecret
    FROM qbo.Client
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboClientByClientId @ClientId = 'test-client';



DROP PROCEDURE IF EXISTS UpdateQboClientByClientId;
GO

CREATE PROCEDURE UpdateQboClientByClientId
(
    @ClientId NVARCHAR(MAX),
    @ClientSecret NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    UPDATE qbo.Client
    SET ClientId = @ClientId,
        ClientSecret = @ClientSecret
    OUTPUT
        INSERTED.ClientId,
        INSERTED.ClientSecret
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateQboClientByClientId
    @ClientId = 'updated-client',
    @ClientSecret = 'updated-secret';


DROP PROCEDURE IF EXISTS DeleteQboClientByClientId;
GO

CREATE PROCEDURE DeleteQboClientByClientId
(
    @ClientId NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM qbo.Client
    OUTPUT
        DELETED.ClientId,
        DELETED.ClientSecret
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteQboClientByClientId
    @ClientId = 'updated-client';
