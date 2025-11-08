IF OBJECT_ID('dbo.client', 'U') IS NOT NULL
    DROP TABLE dbo.client;
GO

CREATE TABLE dbo.client
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

    INSERT INTO dbo.client (ClientId, ClientSecret)
    OUTPUT
        INSERTED.ClientId,
        INSERTED.ClientSecret
    VALUES (@ClientId, @ClientSecret);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboClient @ClientId = 'test-client', @ClientSecret = 'secret';


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
    FROM dbo.client;

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
    FROM dbo.client
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

    UPDATE dbo.client
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

    DELETE FROM dbo.client
    OUTPUT
        DELETED.ClientId,
        DELETED.ClientSecret
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteQboClientByClientId
    @ClientId = 'updated-client';
