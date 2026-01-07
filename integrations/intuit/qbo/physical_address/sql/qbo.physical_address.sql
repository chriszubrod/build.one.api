DROP TABLE IF EXISTS [qbo].[PhysicalAddress];
GO

CREATE TABLE [qbo].[PhysicalAddress]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(MAX) NULL,
    [Line1] NVARCHAR(MAX) NULL,
    [Line2] NVARCHAR(MAX) NULL,
    [City] NVARCHAR(MAX) NULL,
    [Country] NVARCHAR(MAX) NULL,
    [CountrySubDivisionCode] NVARCHAR(MAX) NULL,
    [PostalCode] NVARCHAR(MAX) NULL
);



DROP PROCEDURE IF EXISTS CreateQboPhysicalAddress;
GO

CREATE PROCEDURE CreateQboPhysicalAddress
(
    @QboId NVARCHAR(MAX),
    @Line1 NVARCHAR(MAX),
    @Line2 NVARCHAR(MAX),
    @City NVARCHAR(MAX),
    @Country NVARCHAR(MAX),
    @CountrySubDivisionCode NVARCHAR(MAX),
    @PostalCode NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO [qbo].[PhysicalAddress] ([CreatedDatetime], [ModifiedDatetime], [QboId], [Line1], [Line2], [City], [Country], [CountrySubDivisionCode], [PostalCode])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[Line1],
        INSERTED.[Line2],
        INSERTED.[City],
        INSERTED.[Country],
        INSERTED.[CountrySubDivisionCode],
        INSERTED.[PostalCode]
    VALUES (@Now, @Now, @QboId, @Line1, @Line2, @City, @Country, @CountrySubDivisionCode, @PostalCode);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboPhysicalAddress
    @QboId = '1',
    @Line1 = '123 Main St',
    @Line2 = 'Suite 100',
    @City = 'San Francisco',
    @Country = 'USA',
    @CountrySubDivisionCode = 'CA',
    @PostalCode = '94105';
GO



DROP PROCEDURE IF EXISTS ReadQboPhysicalAddresses;
GO

CREATE PROCEDURE ReadQboPhysicalAddresses
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    ORDER BY [QboId] ASC;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboPhysicalAddresses;
GO


DROP PROCEDURE IF EXISTS ReadQboPhysicalAddressById;
GO

CREATE PROCEDURE ReadQboPhysicalAddressById
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
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboPhysicalAddressById @Id = '1';



DROP PROCEDURE IF EXISTS ReadQboPhysicalAddressByPublicId;
GO

CREATE PROCEDURE ReadQboPhysicalAddressByPublicId
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
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboPhysicalAddressByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO




DROP PROCEDURE IF EXISTS ReadQboPhysicalAddressByQboId;
GO

CREATE PROCEDURE ReadQboPhysicalAddressByQboId
(
    @QboId NVARCHAR(MAX)
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
        [QboId],
        [Line1],
        [Line2],
        [City],
        [Country],
        [CountrySubDivisionCode],
        [PostalCode]
    FROM [qbo].[PhysicalAddress]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboPhysicalAddressByQboId
    @QboId = '1';
GO




DROP PROCEDURE IF EXISTS UpdateQboPhysicalAddressById;
GO

CREATE PROCEDURE UpdateQboPhysicalAddressById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @QboId NVARCHAR(MAX),
    @Line1 NVARCHAR(MAX),
    @Line2 NVARCHAR(MAX),
    @City NVARCHAR(MAX),
    @Country NVARCHAR(MAX),
    @CountrySubDivisionCode NVARCHAR(MAX),
    @PostalCode NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE [qbo].[PhysicalAddress]
    SET [ModifiedDatetime] = @Now,
        [QboId] = @QboId,
        [Line1] = @Line1,
        [Line2] = @Line2,
        [City] = @City,
        [Country] = @Country,
        [CountrySubDivisionCode] = @CountrySubDivisionCode,
        [PostalCode] = @PostalCode
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[Line1],
        INSERTED.[Line2],
        INSERTED.[City],
        INSERTED.[Country],
        INSERTED.[CountrySubDivisionCode],
        INSERTED.[PostalCode]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateQboPhysicalAddressById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @QboId = '1',
    @Line1 = '123 Main St',
    @Line2 = 'Suite 100',
    @City = 'San Francisco',
    @Country = 'USA',
    @CountrySubDivisionCode = 'CA',
    @PostalCode = '94105';
GO



DROP PROCEDURE IF EXISTS DeleteQboPhysicalAddressById;
GO

CREATE PROCEDURE DeleteQboPhysicalAddressById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[PhysicalAddress]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[Line1],
        DELETED.[Line2],
        DELETED.[City],
        DELETED.[Country],
        DELETED.[CountrySubDivisionCode],
        DELETED.[PostalCode]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteQboPhysicalAddressById
    @Id = 1;
GO

