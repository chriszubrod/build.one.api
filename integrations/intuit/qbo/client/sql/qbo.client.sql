IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'qbo')
    EXEC('CREATE SCHEMA qbo AUTHORIZATION dbo;');
GO




IF OBJECT_ID('qbo.Client', 'U') IS NULL
BEGIN
CREATE TABLE qbo.Client
(
    App NVARCHAR(MAX) NOT NULL,
    ClientId NVARCHAR(MAX) NOT NULL,
    ClientSecret NVARCHAR(MAX) NOT NULL
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateQboClient
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


CREATE OR ALTER PROCEDURE ReadQboClients
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


CREATE OR ALTER PROCEDURE ReadQboClientByApp
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


CREATE OR ALTER PROCEDURE ReadQboClientByClientId
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


CREATE OR ALTER PROCEDURE UpdateQboClientByApp
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
    SET App = CASE WHEN @App IS NULL THEN App ELSE @App END,
        ClientId = CASE WHEN @ClientId IS NULL THEN ClientId ELSE @ClientId END,
        ClientSecret = CASE WHEN @ClientSecret IS NULL THEN ClientSecret ELSE @ClientSecret END
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret
    WHERE App = @App;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateQboClientByClientId
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
    SET App = CASE WHEN @App IS NULL THEN App ELSE @App END,
        ClientId = CASE WHEN @ClientId IS NULL THEN ClientId ELSE @ClientId END,
        ClientSecret = CASE WHEN @ClientSecret IS NULL THEN ClientSecret ELSE @ClientSecret END
    OUTPUT
        INSERTED.App,
        INSERTED.ClientId,
        INSERTED.ClientSecret
    WHERE ClientId = @ClientId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteQboClientByApp
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
