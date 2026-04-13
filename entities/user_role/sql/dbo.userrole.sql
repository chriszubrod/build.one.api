IF OBJECT_ID('dbo.UserRole', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[UserRole]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [UserId] BIGINT NOT NULL,
    [RoleId] BIGINT NOT NULL
);
END
GO


GO


GO

CREATE OR ALTER PROCEDURE CreateUserRole
(
    @UserId BIGINT,
    @RoleId BIGINT
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



GO

CREATE OR ALTER PROCEDURE ReadUserRoles
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



GO

CREATE OR ALTER PROCEDURE ReadUserRoleById
(
    @Id BIGINT
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



GO

CREATE OR ALTER PROCEDURE ReadUserRoleByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadUserRoleByUserId
(
    @UserId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
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



GO

CREATE OR ALTER PROCEDURE ReadUserRoleByRoleId
(
    @RoleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
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



GO

CREATE OR ALTER PROCEDURE UpdateUserRoleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @RoleId BIGINT
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



GO

CREATE OR ALTER PROCEDURE DeleteUserRoleById
(
    @Id BIGINT
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


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_User')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserRole_Role')
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [FK_UserRole_Role] FOREIGN KEY ([RoleId]) REFERENCES [dbo].[Role]([Id]);
END
GO

-- Prevent duplicate user-role assignments
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_UserRole_UserId_RoleId' AND parent_object_id = OBJECT_ID('dbo.UserRole'))
BEGIN
    ALTER TABLE [dbo].[UserRole] ADD CONSTRAINT [UQ_UserRole_UserId_RoleId] UNIQUE ([UserId], [RoleId]);
END
GO
