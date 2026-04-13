IF OBJECT_ID('dbo.UserModule', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[UserModule]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [UserId] BIGINT NOT NULL,
    [ModuleId] BIGINT NOT NULL
);
END
GO


GO


GO

CREATE OR ALTER PROCEDURE CreateUserModule
(
    @UserId BIGINT,
    @ModuleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[UserModule] ([CreatedDatetime], [ModifiedDatetime], [UserId], [ModuleId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ModuleId]
    VALUES (@Now, @Now, @UserId, @ModuleId);

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserModules
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
        [ModuleId]
    FROM dbo.[UserModule]
    ORDER BY [UserId] ASC, [ModuleId] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserModuleById
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
        [ModuleId]
    FROM dbo.[UserModule]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserModuleByPublicId
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
        [ModuleId]
    FROM dbo.[UserModule]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserModuleByUserId
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
        [ModuleId]
    FROM dbo.[UserModule]
    WHERE [UserId] = @UserId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserModulesByUserId
(
    @UserId BIGINT
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
        [ModuleId]
    FROM dbo.[UserModule]
    WHERE [UserId] = @UserId
    ORDER BY [ModuleId] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserModuleByModuleId
(
    @ModuleId BIGINT
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
        [ModuleId]
    FROM dbo.[UserModule]
    WHERE [ModuleId] = @ModuleId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateUserModuleById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @ModuleId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[UserModule]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [ModuleId] = @ModuleId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ModuleId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteUserModuleById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[UserModule]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[ModuleId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserModule_User')
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD CONSTRAINT [FK_UserModule_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserModule_Module')
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD CONSTRAINT [FK_UserModule_Module] FOREIGN KEY ([ModuleId]) REFERENCES [dbo].[Module]([Id]);
END
GO

-- Prevent duplicate user-module assignments
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_UserModule_UserId_ModuleId' AND parent_object_id = OBJECT_ID('dbo.UserModule'))
BEGIN
    ALTER TABLE [dbo].[UserModule] ADD CONSTRAINT [UQ_UserModule_UserId_ModuleId] UNIQUE ([UserId], [ModuleId]);
END
GO
