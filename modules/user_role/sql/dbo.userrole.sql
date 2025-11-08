CREATE TABLE [dbo].[UserRole]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [UserId] UNIQUEIDENTIFIER NOT NULL,
    [RoleId] UNIQUEIDENTIFIER NOT NULL
);
GO


DROP TABLE IF EXISTS dbo.[UserRole];
GO


DROP PROCEDURE IF EXISTS CreateUserRole;
GO

CREATE PROCEDURE CreateUserRole
(
    @UserId UNIQUEIDENTIFIER,
    @RoleId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[UserRole] ([CreatedDatetime], [ModifiedDatetime], [UserId], [RoleId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[RoleId]
    VALUES (@Now, @Now, @UserId, @RoleId);

    COMMIT TRANSACTION;
END;

EXEC CreateUserRole
    @UserId = '00000000-0000-0000-0000-000000000000',
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadUserRoles;
GO

CREATE PROCEDURE ReadUserRoles
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [RoleId]
    FROM dbo.[UserRole]
    ORDER BY [UserId] ASC, [RoleId] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadUserRoles;
GO


DROP PROCEDURE IF EXISTS ReadUserRoleById;
GO

CREATE PROCEDURE ReadUserRoleById
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
        [UserId],
        [RoleId]
    FROM dbo.[UserRole]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadUserRoleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadUserRoleByPublicId;
GO

CREATE PROCEDURE ReadUserRoleByPublicId
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
        [UserId],
        [RoleId]
    FROM dbo.[UserRole]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadUserRoleByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadUserRoleByUserId;
GO

CREATE PROCEDURE ReadUserRoleByUserId
(
    @UserId UNIQUEIDENTIFIER
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
        [UserId],
        [RoleId]
    FROM dbo.[UserRole]
    WHERE [UserId] = @UserId;

    COMMIT TRANSACTION;
END;

EXEC ReadUserRoleByUserId
    @UserId = '0000000';
GO


DROP PROCEDURE IF EXISTS ReadUserRoleByRoleId;
GO

CREATE PROCEDURE ReadUserRoleByRoleId
(
    @RoleId UNIQUEIDENTIFIER
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
        [UserId],
        [RoleId]
    FROM dbo.[UserRole]
    WHERE [RoleId] = @RoleId;

    COMMIT TRANSACTION;
END;

EXEC ReadUserRoleByRoleId
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS UpdateUserRoleById;
GO

CREATE PROCEDURE UpdateUserRoleById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @UserId UNIQUEIDENTIFIER,
    @RoleId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[UserRole]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [RoleId] = @RoleId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[RoleId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateUserRoleById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @UserId = '00000000-0000-0000-0000-000000000000',
    @RoleId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS DeleteUserRoleById;
GO

CREATE PROCEDURE DeleteUserRoleById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[UserRole]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[RoleId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteUserRoleById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
