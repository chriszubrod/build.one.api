CREATE TABLE dbo.[Auth]
(
    [Id] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Username] NVARCHAR(255) NOT NULL,
    [PasswordHash] NVARCHAR(255) NOT NULL,
    CONSTRAINT UQ_Auth_Username UNIQUE (Username)
);
GO



DROP PROCEDURE IF EXISTS dbo.CreateAuth;
GO

CREATE PROCEDURE dbo.CreateAuth
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
        INSERTED.[PasswordHash]
    VALUES (@Now, @Now, @Username, @PasswordHash);

    COMMIT TRANSACTION;
END;
GO




DROP PROCEDURE IF EXISTS dbo.ReadAuthByPublicId;
GO

CREATE PROCEDURE dbo.ReadAuthByPublicId
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
        [PasswordHash]
    FROM dbo.[Auth]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadAuthByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO





DROP PROCEDURE IF EXISTS dbo.ReadAuthByUsername;
GO

CREATE PROCEDURE dbo.ReadAuthByUsername
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
        [PasswordHash]
    FROM dbo.[Auth]
    WHERE [Username] = @Username;

    COMMIT TRANSACTION;
END;

EXEC ReadAuthByUsername
    @Username = 'sample-user';
GO




DROP PROCEDURE IF EXISTS dbo.UpdateAuthById;
GO

CREATE PROCEDURE dbo.UpdateAuthById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @Username NVARCHAR(255),
    @PasswordHash NVARCHAR(255)
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
        [PasswordHash] = @PasswordHash
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Username],
        INSERTED.[PasswordHash]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateAuthById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = '00000000-0000-0000-0000-000000000000',
    @Username = 'sample-user',
    @PasswordHash = 'sample-password-hash';
GO




DROP PROCEDURE IF EXISTS dbo.DeleteAuthById;
GO

CREATE PROCEDURE dbo.DeleteAuthById
(
    @Id UNIQUEIDENTIFIER
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
        DELETED.[PasswordHash]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteAuthById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


