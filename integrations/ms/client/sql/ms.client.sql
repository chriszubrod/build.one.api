IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ms')
    EXEC('CREATE SCHEMA ms AUTHORIZATION dbo;');
GO

IF OBJECT_ID('ms.Client', 'U') IS NOT NULL
GO

IF OBJECT_ID('ms.Client', 'U') IS NULL
BEGIN
CREATE TABLE ms.Client
(
    App NVARCHAR(MAX) NOT NULL,
    ClientId NVARCHAR(MAX) NOT NULL,
    ClientSecret NVARCHAR(MAX) NOT NULL,
    TenantId NVARCHAR(MAX) NOT NULL,
    RedirectUri NVARCHAR(MAX) NOT NULL
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateMsClient
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


CREATE OR ALTER PROCEDURE ReadMsClients
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


CREATE OR ALTER PROCEDURE ReadMsClientByApp
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


CREATE OR ALTER PROCEDURE ReadMsClientByClientId
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


CREATE OR ALTER PROCEDURE UpdateMsClientByApp
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


CREATE OR ALTER PROCEDURE UpdateMsClientByClientId
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


CREATE OR ALTER PROCEDURE DeleteMsClientByApp
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

    @App = 'buildone';
