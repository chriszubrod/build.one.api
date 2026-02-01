IF OBJECT_ID('dbo.Auth', 'U') IS NULL
BEGIN
CREATE TABLE dbo.[Auth]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Username] NVARCHAR(255) NOT NULL,
    [PasswordHash] NVARCHAR(255) NOT NULL,
    [UserId] BIGINT NULL
);
END
GO

CREATE OR ALTER PROCEDURE dbo.CreateAuth
(
    @Username NVARCHAR(255),
    @PasswordHash NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    
    INSERT INTO dbo.Auth ([CreatedDatetime], [ModifiedDatetime], [Username], [PasswordHash])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Username],
        INSERTED.[PasswordHash],
        INSERTED.[UserId]
    VALUES (@Now, @Now, @Username, @PasswordHash);

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE dbo.ReadAuths
AS
BEGIN
    BEGIN TRANSACTION;
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Username],
        [PasswordHash],
        [UserId]
    FROM dbo.[Auth]
    ORDER BY [Username] ASC;

    COMMIT TRANSACTION;
END;
GO





IF OBJECT_ID('dbo.AuthRefreshToken', 'U') IS NULL
BEGIN
CREATE TABLE dbo.AuthRefreshToken
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [AuthId] BIGINT NOT NULL,
    [TokenHash] CHAR(64) NOT NULL,
    [TokenJti] UNIQUEIDENTIFIER NOT NULL,
    [IssuedDatetime] DATETIME2(3) NOT NULL,
    [ExpiresDatetime] DATETIME2(3) NOT NULL,
    [RevokedDatetime] DATETIME2(3) NULL,
    [ReplacedByTokenJti] UNIQUEIDENTIFIER NULL
);
END
GO



IF OBJECT_ID('dbo.FK_AuthRefreshToken_Auth', 'F') IS NULL
BEGIN
ALTER TABLE dbo.AuthRefreshToken WITH CHECK
ADD CONSTRAINT FK_AuthRefreshToken_Auth
FOREIGN KEY ([AuthId]) REFERENCES dbo.[Auth]([Id]);
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UX_AuthRefreshToken_TokenHash' AND object_id = OBJECT_ID('dbo.AuthRefreshToken')
)
BEGIN
    CREATE UNIQUE INDEX UX_AuthRefreshToken_TokenHash
    ON dbo.AuthRefreshToken ([TokenHash]);
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_AuthRefreshToken_AuthId' AND object_id = OBJECT_ID('dbo.AuthRefreshToken')
)
BEGIN
    CREATE INDEX IX_AuthRefreshToken_AuthId
    ON dbo.AuthRefreshToken ([AuthId]);
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_AuthRefreshToken_ExpiresDatetime' AND object_id = OBJECT_ID('dbo.AuthRefreshToken')
)
BEGIN
    CREATE INDEX IX_AuthRefreshToken_ExpiresDatetime
    ON dbo.AuthRefreshToken ([ExpiresDatetime]);
END
GO

CREATE OR ALTER PROCEDURE dbo.CreateAuthRefreshToken
(
    @AuthId BIGINT,
    @TokenHash CHAR(64),
    @TokenJti UNIQUEIDENTIFIER,
    @IssuedDatetime DATETIME2(3),
    @ExpiresDatetime DATETIME2(3),
    @RevokedDatetime DATETIME2(3) = NULL,
    @ReplacedByTokenJti UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    INSERT INTO dbo.AuthRefreshToken
    (
        [AuthId],
        [TokenHash],
        [TokenJti],
        [IssuedDatetime],
        [ExpiresDatetime],
        [RevokedDatetime],
        [ReplacedByTokenJti]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[AuthId],
        INSERTED.[TokenHash],
        INSERTED.[TokenJti],
        INSERTED.[IssuedDatetime],
        INSERTED.[ExpiresDatetime],
        INSERTED.[RevokedDatetime],
        INSERTED.[ReplacedByTokenJti]
    VALUES
    (
        @AuthId,
        @TokenHash,
        @TokenJti,
        @IssuedDatetime,
        @ExpiresDatetime,
        @RevokedDatetime,
        @ReplacedByTokenJti
    );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadAuthRefreshTokenByHash
(
    @TokenHash CHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [AuthId],
        [TokenHash],
        [TokenJti],
        [IssuedDatetime],
        [ExpiresDatetime],
        [RevokedDatetime],
        [ReplacedByTokenJti]
    FROM dbo.AuthRefreshToken
    WHERE [TokenHash] = @TokenHash;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE dbo.RevokeAuthRefreshTokenByHash
(
    @TokenHash CHAR(64),
    @RevokedDatetime DATETIME2(3),
    @ReplacedByTokenJti UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    UPDATE dbo.AuthRefreshToken
    SET
        [RevokedDatetime] = @RevokedDatetime,
        [ReplacedByTokenJti] = @ReplacedByTokenJti
    OUTPUT
        INSERTED.[Id],
        INSERTED.[AuthId],
        INSERTED.[TokenHash],
        INSERTED.[TokenJti],
        INSERTED.[IssuedDatetime],
        INSERTED.[ExpiresDatetime],
        INSERTED.[RevokedDatetime],
        INSERTED.[ReplacedByTokenJti]
    WHERE [TokenHash] = @TokenHash AND [RevokedDatetime] IS NULL;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE dbo.ReadAuthByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Username],
        [PasswordHash],
        [UserId]
    FROM dbo.[Auth]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE dbo.ReadAuthByUsername
(
    @Username NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Username],
        [PasswordHash],
        [UserId]
    FROM dbo.[Auth]
    WHERE [Username] = @Username;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE dbo.UpdateAuthById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Username NVARCHAR(255),
    @PasswordHash NVARCHAR(255),
    @UserId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Auth]
    SET
        [ModifiedDatetime] = @Now,
        [Username] = @Username,
        [PasswordHash] = @PasswordHash,
        [UserId] = @UserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Username],
        INSERTED.[PasswordHash],
        INSERTED.[UserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE dbo.DeleteAuthById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    SET NOCOUNT ON;

    DELETE FROM dbo.[Auth]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Username],
        DELETED.[PasswordHash],
        DELETED.[UserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


