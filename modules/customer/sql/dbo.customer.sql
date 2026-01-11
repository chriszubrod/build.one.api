DROP TABLE IF EXISTS dbo.[Customer];
GO



CREATE TABLE [dbo].[Customer]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Email] NVARCHAR(255) NULL,
    [Phone] NVARCHAR(50) NULL
);
GO


DROP PROCEDURE IF EXISTS CreateCustomer;
GO

CREATE PROCEDURE CreateCustomer
(
    @Name NVARCHAR(50),
    @Email NVARCHAR(255),
    @Phone NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Customer] ([CreatedDatetime], [ModifiedDatetime], [Name], [Email], [Phone])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Email],
        INSERTED.[Phone]
    VALUES (@Now, @Now, @Name, @Email, @Phone);

    COMMIT TRANSACTION;
END;

EXEC CreateCustomer
    @Name = 'John Doe',
    @Email = 'john.doe@example.com',
    @Phone = '555-1234';
GO


DROP PROCEDURE IF EXISTS ReadCustomers;
GO

CREATE PROCEDURE ReadCustomers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Name],
        [Email],
        [Phone]
    FROM dbo.[Customer]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadCustomers;
GO


DROP PROCEDURE IF EXISTS ReadCustomerById;
GO

CREATE PROCEDURE ReadCustomerById
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
        [Name],
        [Email],
        [Phone]
    FROM dbo.[Customer]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadCustomerById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadCustomerByPublicId;
GO

CREATE PROCEDURE ReadCustomerByPublicId
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
        [Name],
        [Email],
        [Phone]
    FROM dbo.[Customer]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadCustomerByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadCustomerByName;
GO

CREATE PROCEDURE ReadCustomerByName
(
    @Name NVARCHAR(50)
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
        [Name],
        [Email],
        [Phone]
    FROM dbo.[Customer]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadCustomerByName
    @Name = 'John Doe';
GO


DROP PROCEDURE IF EXISTS UpdateCustomerById;
GO

CREATE PROCEDURE UpdateCustomerById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Email NVARCHAR(255),
    @Phone NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Customer]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Email] = @Email,
        [Phone] = @Phone
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Email],
        INSERTED.[Phone]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateCustomerById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @Name = 'Jane Doe',
    @Email = 'jane.doe@example.com',
    @Phone = '555-5678';
GO


DROP PROCEDURE IF EXISTS DeleteCustomerById;
GO

CREATE PROCEDURE DeleteCustomerById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Customer]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Email],
        DELETED.[Phone]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteCustomerById
    @Id = 3;
GO
