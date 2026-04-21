IF OBJECT_ID('qbo.Auth', 'U') IS NULL
BEGIN
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
END
GO


GO

CREATE OR ALTER PROCEDURE CreateQboAuth
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


CREATE OR ALTER PROCEDURE ReadQboAuths
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


CREATE OR ALTER PROCEDURE ReadQboAuthById
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




GO

CREATE OR ALTER PROCEDURE ReadQboAuthByPublicId
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




GO

CREATE OR ALTER PROCEDURE ReadQboAuthByRealmId
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


CREATE OR ALTER PROCEDURE UpdateQboAuthByRealmId
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
        Code = CASE WHEN @Code IS NULL THEN Code ELSE @Code END,
        RealmId = CASE WHEN @RealmId IS NULL THEN RealmId ELSE @RealmId END,
        [State] = CASE WHEN @State IS NULL THEN [State] ELSE @State END,
        TokenType = CASE WHEN @TokenType IS NULL THEN TokenType ELSE @TokenType END,
        IdToken = CASE WHEN @IdToken IS NULL THEN IdToken ELSE @IdToken END,
        AccessToken = CASE WHEN @AccessToken IS NULL THEN AccessToken ELSE @AccessToken END,
        ExpiresIn = CASE WHEN @ExpiresIn IS NULL THEN ExpiresIn ELSE @ExpiresIn END,
        RefreshToken = CASE WHEN @RefreshToken IS NULL THEN RefreshToken ELSE @RefreshToken END,
        XRefreshTokenExpiresIn = CASE WHEN @XRefreshTokenExpiresIn IS NULL THEN XRefreshTokenExpiresIn ELSE @XRefreshTokenExpiresIn END
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


CREATE OR ALTER PROCEDURE DeleteQboAuthByRealmId
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
