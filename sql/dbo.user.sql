CREATE TABLE [dbo].[User]
(
    [Id] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Firstname] NVARCHAR(50) NOT NULL,
    [Lastname] NVARCHAR(255) NOT NULL
);
GO



DROP PROCEDURE IF EXISTS CreateUser;
GO

CREATE PROCEDURE CreateUser
(
    @Firstname NVARCHAR(50),
    @Lastname NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[User] ([CreatedDatetime], [ModifiedDatetime], [Firstname], [Lastname])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname]
    VALUES (@Now, @Now, @Firstname, @Lastname);

    COMMIT TRANSACTION;
END;

EXEC CreateUser
    @Firstname = 'John',
    @Lastname = 'Doe';
GO


DROP PROCEDURE IF EXISTS ReadUsers;
GO

CREATE PROCEDURE ReadUsers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    ORDER BY [Lastname] ASC, [Firstname] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadUsers;
GO


DROP PROCEDURE IF EXISTS ReadUserById;
GO

CREATE PROCEDURE ReadUserById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadUserById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadUserByPublicId;
GO

CREATE PROCEDURE ReadUserByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadUserByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadUserByFirstname;
GO

CREATE PROCEDURE ReadUserByFirstname
(
    @Firstname NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Firstname] = @Firstname;

    COMMIT TRANSACTION;
END;

EXEC ReadUserByFirstname
    @Firstname = 'John';
GO


DROP PROCEDURE IF EXISTS ReadUserByLastname;
GO

CREATE PROCEDURE ReadUserByLastname
(
    @Lastname NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Lastname] = @Lastname;

    COMMIT TRANSACTION;
END;

EXEC ReadUserByLastname
    @Lastname = 'Doe';
GO


DROP PROCEDURE IF EXISTS UpdateUserById;
GO

CREATE PROCEDURE UpdateUserById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @Firstname NVARCHAR(50),
    @Lastname NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[User]
    SET
        [ModifiedDatetime] = @Now,
        [Firstname] = @Firstname,
        [Lastname] = @Lastname
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateUserById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @Firstname = 'John',
    @Lastname = 'Doe';
GO


DROP PROCEDURE IF EXISTS DeleteUserById;
GO

CREATE PROCEDURE DeleteUserById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[User]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Firstname],
        DELETED.[Lastname]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteUserById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
