IF OBJECT_ID('qbo.Auth', 'U') IS NOT NULL
    DROP TABLE qbo.Auth;
GO

CREATE TABLE qbo.Auth
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
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

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO qbo.Auth ([CreatedDatetime], [ModifiedDatetime], Code, RealmId, State, TokenType, IdToken, AccessToken, ExpiresIn, RefreshToken, XRefreshTokenExpiresIn)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.Code,
        INSERTED.RealmId,
        INSERTED.State,
        INSERTED.TokenType,
        INSERTED.IdToken,
        INSERTED.AccessToken,
        INSERTED.ExpiresIn,
        INSERTED.RefreshToken,
        INSERTED.XRefreshTokenExpiresIn
    VALUES (@Now, @Now, @Code, @RealmId, @State, @TokenType, @IdToken, @AccessToken, @ExpiresIn, @RefreshToken, @XRefreshTokenExpiresIn);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboAuth
    @Code = '1234567890',
    @RealmId = '1234567890',
    @State = '1234567890',
    @TokenType = '1234567890',
    @IdToken = '1234567890',
    @AccessToken = '1234567890',
    @ExpiresIn = 1234567890,
    @RefreshToken = '1234567890',
    @XRefreshTokenExpiresIn = 1234567890;


DROP PROCEDURE IF EXISTS ReadQboAuths;
GO

CREATE PROCEDURE ReadQboAuths
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
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


DROP PROCEDURE IF EXISTS ReadQboAuthById;
GO

CREATE PROCEDURE ReadQboAuthById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
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
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboAuthById
    @Id = 1;
GO



DROP PROCEDURE IF EXISTS ReadQboAuthByPublicId;
GO

CREATE PROCEDURE ReadQboAuthByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
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
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboAuthByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO



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
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
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

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE qbo.Auth
    SET [ModifiedDatetime] = @Now,
        Code = @Code,
        RealmId = @RealmId,
        [State] = @State,
        TokenType = @TokenType,
        IdToken = @IdToken,
        AccessToken = @AccessToken,
        ExpiresIn = @ExpiresIn,
        RefreshToken = @RefreshToken,
        XRefreshTokenExpiresIn = @XRefreshTokenExpiresIn
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
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

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    DELETE FROM qbo.Auth
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
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
