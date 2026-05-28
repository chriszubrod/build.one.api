IF OBJECT_ID('dbo.User', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[User]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Firstname] NVARCHAR(50) NOT NULL,
    [Lastname] NVARCHAR(255) NOT NULL,
    -- Worker linkage — at most one of EmployeeId / VendorId is non-NULL per User.
    -- XOR enforced in the Python service layer (UserService.set_worker_link); we
    -- skip a CHECK constraint so admin tooling can flip a row through a neutral
    -- (both-NULL) state without needing an atomic transaction.
    [EmployeeId] BIGINT NULL,
    [VendorId]   BIGINT NULL
);
END
GO

-- Idempotent column adds for existing environments.
IF COL_LENGTH('dbo.[User]', 'EmployeeId') IS NULL
    ALTER TABLE [dbo].[User] ADD [EmployeeId] BIGINT NULL;
GO

IF COL_LENGTH('dbo.[User]', 'VendorId') IS NULL
    ALTER TABLE [dbo].[User] ADD [VendorId] BIGINT NULL;
GO

-- FK constraints — Employee must exist before User can be backfilled.
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_Employee')
   AND OBJECT_ID('dbo.[Employee]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[User]
    ADD CONSTRAINT [FK_User_Employee] FOREIGN KEY ([EmployeeId]) REFERENCES [dbo].[Employee]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_Vendor')
   AND OBJECT_ID('dbo.[Vendor]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[User]
    ADD CONSTRAINT [FK_User_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]);
END
GO

-- Filtered unique indexes prevent two Users from claiming the same worker row.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_User_EmployeeId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    CREATE UNIQUE INDEX [UX_User_EmployeeId] ON [dbo].[User] ([EmployeeId]) WHERE [EmployeeId] IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_User_VendorId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    CREATE UNIQUE INDEX [UX_User_VendorId] ON [dbo].[User] ([VendorId]) WHERE [VendorId] IS NOT NULL;
END
GO



GO

CREATE OR ALTER PROCEDURE CreateUser
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
GO


CREATE OR ALTER PROCEDURE ReadUsers
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



GO

CREATE OR ALTER PROCEDURE ReadUserById
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
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadUserByFirstname
(
    @Firstname NVARCHAR(50)
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
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Firstname] = @Firstname;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserByLastname
(
    @Lastname NVARCHAR(255)
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
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Lastname] = @Lastname;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateUserById
(
    @Id BIGINT,
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



GO

CREATE OR ALTER PROCEDURE DeleteUserById
(
    @Id BIGINT
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

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_User_PublicId' AND object_id = OBJECT_ID('dbo.User'))
BEGIN
    CREATE INDEX [IX_User_PublicId] ON [dbo].[User] ([PublicId]);
END
GO

