IF OBJECT_ID('qbo.Auth', 'U') IS NOT NULL
    DROP TABLE qbo.Auth;
GO

CREATE TABLE qbo.Auth
(
    Code NVARCHAR(MAX) NOT NULL,
    RealmId NVARCHAR(MAX) NOT NULL,
    [State] NVARCHAR(MAX) NOT NULL,
    TokenType NVARCHAR(MAX) NOT NULL,
    IdToken NVARCHAR(MAX) NOT NULL,
    AccessToken NVARCHAR(MAX) NOT NULL,
    ExpiresIn INT NOT NULL,
    RefreshToken NVARCHAR(MAX) NOT NULL,
    XRefreshTokenExpiresIn INT NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateQboAuth;
GO

CREATE PROCEDURE CreateQboAuth
(
    @Code NVARCHAR(MAX),
    @RealmId NVARCHAR(MAX),
    @State NVARCHAR(MAX),
    @TokenType NVARCHAR(MAX),
    @IdToken NVARCHAR(MAX),
    @AccessToken NVARCHAR(MAX),
    @ExpiresIn INT,
    @RefreshToken NVARCHAR(MAX),
    @XRefreshTokenExpiresIn INT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    INSERT INTO qbo.Auth (Code, RealmId, State, TokenType, IdToken, AccessToken, ExpiresIn, RefreshToken, XRefreshTokenExpiresIn)
    OUTPUT
        INSERTED.Code,
        INSERTED.RealmId,
        INSERTED.State,
        INSERTED.TokenType,
        INSERTED.IdToken,
        INSERTED.AccessToken,
        INSERTED.ExpiresIn,
        INSERTED.RefreshToken,
        INSERTED.XRefreshTokenExpiresIn
    VALUES (@Code, @RealmId, @State, @TokenType, @IdToken, @AccessToken, @ExpiresIn, @RefreshToken, @XRefreshTokenExpiresIn);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboAuth @Code = '1234567890', @RealmId = '1234567890', @State = '1234567890', @TokenType = '1234567890', @IdToken = '1234567890', @AccessToken = '1234567890', @ExpiresIn = 1234567890, @RefreshToken = '1234567890', @XRefreshTokenExpiresIn = 1234567890;


DROP PROCEDURE IF EXISTS ReadQboAuths;
GO

CREATE PROCEDURE ReadQboAuths
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        Code,
        RealmId,
        [State],
        TokenType,
        IdToken,
        AccessToken,
        ExpiresIn,
        RefreshToken,
        XRefreshTokenExpiresIn
    FROM qbo.Auth;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboAuths;


DROP PROCEDURE IF EXISTS ReadQboAuthByRealmId;
GO

CREATE PROCEDURE ReadQboAuthByRealmId
(
    @RealmId NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        Code,
        RealmId,
        [State],
        TokenType,
        IdToken,
        AccessToken,
        ExpiresIn,
        RefreshToken,
        XRefreshTokenExpiresIn
    FROM qbo.Auth
    WHERE RealmId = @RealmId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboAuthByRealmId @RealmId = '1234567890';


DROP PROCEDURE IF EXISTS UpdateQboAuthByRealmId;
GO

CREATE PROCEDURE UpdateQboAuthByRealmId
(
    @Code NVARCHAR(MAX),
    @RealmId NVARCHAR(MAX),
    @State NVARCHAR(MAX),
    @TokenType NVARCHAR(MAX),
    @IdToken NVARCHAR(MAX),
    @AccessToken NVARCHAR(MAX),
    @ExpiresIn INT,
    @RefreshToken NVARCHAR(MAX),
    @XRefreshTokenExpiresIn INT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    UPDATE qbo.Auth
    SET Code = @Code,
        RealmId = @RealmId,
        [State] = @State,
        TokenType = @TokenType,
        IdToken = @IdToken,
        AccessToken = @AccessToken,
        ExpiresIn = @ExpiresIn,
        RefreshToken = @RefreshToken,
        XRefreshTokenExpiresIn = @XRefreshTokenExpiresIn
    OUTPUT
        INSERTED.Code,
        INSERTED.RealmId,
        INSERTED.[State],
        INSERTED.TokenType,
        INSERTED.IdToken,
        INSERTED.AccessToken,
        INSERTED.ExpiresIn,
        INSERTED.RefreshToken,
        INSERTED.XRefreshTokenExpiresIn
    WHERE RealmId = @RealmId;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateQboAuthByRealmId
    @Code = '1234567890',
    @RealmId = '1234567890',
    @State = '1234567890',
    @TokenType = '1234567890',
    @IdToken = '1234567890',
    @AccessToken = '1234567890',
    @ExpiresIn = 1234567890,
    @RefreshToken = '1234567890',
    @XRefreshTokenExpiresIn = 1234567890;


DROP PROCEDURE IF EXISTS DeleteQboAuthByRealmId;
GO

CREATE PROCEDURE DeleteQboAuthByRealmId
(
    @RealmId NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM qbo.Auth
    OUTPUT
        DELETED.Code,
        DELETED.RealmId,
        DELETED.[State],
        DELETED.TokenType,
        DELETED.IdToken,
        DELETED.AccessToken,
        DELETED.ExpiresIn,
        DELETED.RefreshToken,
        DELETED.XRefreshTokenExpiresIn
    WHERE RealmId = @RealmId;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteQboAuthByRealmId
    @RealmId = '1234567890';
