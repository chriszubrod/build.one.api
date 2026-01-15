IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ms')
    EXEC('CREATE SCHEMA ms AUTHORIZATION dbo;');
GO

IF OBJECT_ID('ms.Auth', 'U') IS NOT NULL
    DROP TABLE ms.Auth;
GO

CREATE TABLE ms.Auth
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    Code NVARCHAR(MAX) NOT NULL,
    [State] NVARCHAR(MAX) NOT NULL,
    TokenType NVARCHAR(MAX) NOT NULL,
    AccessToken NVARCHAR(MAX) NOT NULL,
    ExpiresIn INT NOT NULL,
    RefreshToken NVARCHAR(MAX) NOT NULL,
    Scope NVARCHAR(MAX) NOT NULL,
    TenantId NVARCHAR(MAX) NOT NULL,
    UserId NVARCHAR(MAX) NULL
);
GO


DROP PROCEDURE IF EXISTS CreateMsAuth;
GO

CREATE PROCEDURE CreateMsAuth
(
    @Code NVARCHAR(MAX),
    @State NVARCHAR(MAX),
    @TokenType NVARCHAR(MAX),
    @AccessToken NVARCHAR(MAX),
    @ExpiresIn INT,
    @RefreshToken NVARCHAR(MAX),
    @Scope NVARCHAR(MAX),
    @TenantId NVARCHAR(MAX),
    @UserId NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO ms.Auth ([CreatedDatetime], [ModifiedDatetime], Code, [State], TokenType, AccessToken, ExpiresIn, RefreshToken, Scope, TenantId, UserId)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.Code,
        INSERTED.[State],
        INSERTED.TokenType,
        INSERTED.AccessToken,
        INSERTED.ExpiresIn,
        INSERTED.RefreshToken,
        INSERTED.Scope,
        INSERTED.TenantId,
        INSERTED.UserId
    VALUES (@Now, @Now, @Code, @State, @TokenType, @AccessToken, @ExpiresIn, @RefreshToken, @Scope, @TenantId, @UserId);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateMsAuth
    @Code = 'test-code',
    @State = 'test-state',
    @TokenType = 'Bearer',
    @AccessToken = 'test-access-token',
    @ExpiresIn = 3600,
    @RefreshToken = 'test-refresh-token',
    @Scope = 'Mail.Read Mail.Send Sites.ReadWrite User.Read offline_access',
    @TenantId = 'test-tenant-id',
    @UserId = 'test-user-id';


DROP PROCEDURE IF EXISTS ReadMsAuths;
GO

CREATE PROCEDURE ReadMsAuths
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
        [State],
        TokenType,
        AccessToken,
        ExpiresIn,
        RefreshToken,
        Scope,
        TenantId,
        UserId
    FROM ms.Auth;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsAuths;


DROP PROCEDURE IF EXISTS ReadMsAuthById;
GO

CREATE PROCEDURE ReadMsAuthById
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
        [State],
        TokenType,
        AccessToken,
        ExpiresIn,
        RefreshToken,
        Scope,
        TenantId,
        UserId
    FROM ms.Auth
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsAuthById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadMsAuthByPublicId;
GO

CREATE PROCEDURE ReadMsAuthByPublicId
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
        [State],
        TokenType,
        AccessToken,
        ExpiresIn,
        RefreshToken,
        Scope,
        TenantId,
        UserId
    FROM ms.Auth
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsAuthByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadMsAuthByTenantId;
GO

CREATE PROCEDURE ReadMsAuthByTenantId
(
    @TenantId NVARCHAR(MAX)
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
        [State],
        TokenType,
        AccessToken,
        ExpiresIn,
        RefreshToken,
        Scope,
        TenantId,
        UserId
    FROM ms.Auth
    WHERE TenantId = @TenantId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadMsAuthByTenantId @TenantId = 'test-tenant-id';


DROP PROCEDURE IF EXISTS UpdateMsAuthByTenantId;
GO

CREATE PROCEDURE UpdateMsAuthByTenantId
(
    @Code NVARCHAR(MAX),
    @State NVARCHAR(MAX),
    @TokenType NVARCHAR(MAX),
    @AccessToken NVARCHAR(MAX),
    @ExpiresIn INT,
    @RefreshToken NVARCHAR(MAX),
    @Scope NVARCHAR(MAX),
    @TenantId NVARCHAR(MAX),
    @UserId NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE ms.Auth
    SET [ModifiedDatetime] = @Now,
        Code = @Code,
        [State] = @State,
        TokenType = @TokenType,
        AccessToken = @AccessToken,
        ExpiresIn = @ExpiresIn,
        RefreshToken = @RefreshToken,
        Scope = @Scope,
        UserId = @UserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.Code,
        INSERTED.[State],
        INSERTED.TokenType,
        INSERTED.AccessToken,
        INSERTED.ExpiresIn,
        INSERTED.RefreshToken,
        INSERTED.Scope,
        INSERTED.TenantId,
        INSERTED.UserId
    WHERE TenantId = @TenantId;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateMsAuthByTenantId
    @Code = 'test-code',
    @State = 'test-state',
    @TokenType = 'Bearer',
    @AccessToken = 'test-access-token',
    @ExpiresIn = 3600,
    @RefreshToken = 'test-refresh-token',
    @Scope = 'Mail.Read Mail.Send Sites.ReadWrite User.Read offline_access',
    @TenantId = 'test-tenant-id',
    @UserId = 'test-user-id';


DROP PROCEDURE IF EXISTS DeleteMsAuthByTenantId;
GO

CREATE PROCEDURE DeleteMsAuthByTenantId
(
    @TenantId NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DELETE FROM ms.Auth
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.Code,
        DELETED.[State],
        DELETED.TokenType,
        DELETED.AccessToken,
        DELETED.ExpiresIn,
        DELETED.RefreshToken,
        DELETED.Scope,
        DELETED.TenantId,
        DELETED.UserId
    WHERE TenantId = @TenantId;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteMsAuthByTenantId
    @TenantId = 'test-tenant-id';
