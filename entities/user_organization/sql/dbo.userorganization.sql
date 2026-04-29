IF OBJECT_ID('dbo.UserOrganization', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[UserOrganization]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [UserId] BIGINT NOT NULL,
    [OrganizationId] BIGINT NOT NULL
);
END
GO


CREATE OR ALTER PROCEDURE CreateUserOrganization
(
    @UserId BIGINT,
    @OrganizationId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[UserOrganization] ([CreatedDatetime], [ModifiedDatetime], [UserId], [OrganizationId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[OrganizationId]
    VALUES (@Now, @Now, @UserId, @OrganizationId);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserOrganizations
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
        [OrganizationId]
    FROM dbo.[UserOrganization]
    ORDER BY [UserId] ASC, [OrganizationId] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserOrganizationById
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
        [OrganizationId]
    FROM dbo.[UserOrganization]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserOrganizationByPublicId
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
        [OrganizationId]
    FROM dbo.[UserOrganization]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserOrganizationByUserId
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
        [OrganizationId]
    FROM dbo.[UserOrganization]
    WHERE [UserId] = @UserId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserOrganizationsByUserId
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
        [OrganizationId]
    FROM dbo.[UserOrganization]
    WHERE [UserId] = @UserId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateUserOrganizationById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @OrganizationId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[UserOrganization]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [OrganizationId] = @OrganizationId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[OrganizationId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteUserOrganizationById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[UserOrganization]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[OrganizationId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserOrganization_User')
BEGIN
    ALTER TABLE [dbo].[UserOrganization] ADD CONSTRAINT [FK_UserOrganization_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserOrganization_Organization')
BEGIN
    ALTER TABLE [dbo].[UserOrganization] ADD CONSTRAINT [FK_UserOrganization_Organization] FOREIGN KEY ([OrganizationId]) REFERENCES [dbo].[Organization]([Id]);
END
GO

-- Prevent duplicate user-organization assignments
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_UserOrganization_UserId_OrganizationId' AND parent_object_id = OBJECT_ID('dbo.UserOrganization'))
BEGIN
    ALTER TABLE [dbo].[UserOrganization] ADD CONSTRAINT [UQ_UserOrganization_UserId_OrganizationId] UNIQUE ([UserId], [OrganizationId]);
END
GO
