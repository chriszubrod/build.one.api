IF OBJECT_ID('dbo.UserCompany', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[UserCompany]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [UserId] BIGINT NOT NULL,
    [CompanyId] BIGINT NOT NULL
);
END
GO


CREATE OR ALTER PROCEDURE CreateUserCompany
(
    @UserId BIGINT,
    @CompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[UserCompany] ([CreatedDatetime], [ModifiedDatetime], [UserId], [CompanyId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[CompanyId]
    VALUES (@Now, @Now, @UserId, @CompanyId);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserCompanies
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
        [CompanyId]
    FROM dbo.[UserCompany]
    ORDER BY [UserId] ASC, [CompanyId] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserCompanyById
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
        [CompanyId]
    FROM dbo.[UserCompany]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserCompanyByPublicId
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
        [CompanyId]
    FROM dbo.[UserCompany]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserCompanyByUserId
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
        [CompanyId]
    FROM dbo.[UserCompany]
    WHERE [UserId] = @UserId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUserCompaniesByUserId
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
        [CompanyId]
    FROM dbo.[UserCompany]
    WHERE [UserId] = @UserId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateUserCompanyById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @CompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[UserCompany]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [CompanyId] = @CompanyId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[CompanyId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteUserCompanyById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[UserCompany]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[CompanyId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserCompany_User')
BEGIN
    ALTER TABLE [dbo].[UserCompany] ADD CONSTRAINT [FK_UserCompany_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserCompany_Company')
BEGIN
    ALTER TABLE [dbo].[UserCompany] ADD CONSTRAINT [FK_UserCompany_Company] FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

-- Prevent duplicate user-company assignments
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_UserCompany_UserId_CompanyId' AND parent_object_id = OBJECT_ID('dbo.UserCompany'))
BEGIN
    ALTER TABLE [dbo].[UserCompany] ADD CONSTRAINT [UQ_UserCompany_UserId_CompanyId] UNIQUE ([UserId], [CompanyId]);
END
GO
