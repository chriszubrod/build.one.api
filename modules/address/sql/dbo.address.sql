DROP TABLE IF EXISTS dbo.[Address];
GO

CREATE TABLE [dbo].[Address]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [StreetOne] NVARCHAR(255) NOT NULL,
    [StreetTwo] NVARCHAR(255) NULL,
    [City] NVARCHAR(255) NOT NULL,
    [State] NVARCHAR(2) NOT NULL,
    [Zip] NVARCHAR(5) NOT NULL,
    [Country] NVARCHAR(255) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateAddress;
GO

CREATE PROCEDURE CreateAddress
(
    @StreetOne NVARCHAR(255),
    @StreetTwo NVARCHAR(255),
    @City NVARCHAR(255),
    @State NVARCHAR(2),
    @Zip NVARCHAR(5),
    @Country NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Address] ([CreatedDatetime], [ModifiedDatetime], [StreetOne], [StreetTwo], [City], [State], [Zip], [Country])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[StreetOne],
        INSERTED.[StreetTwo],
        INSERTED.[City],
        INSERTED.[State],
        INSERTED.[Zip],
        INSERTED.[Country]
    VALUES (@Now, @Now, @StreetOne, @StreetTwo, @City, @State, @Zip, @Country);

    COMMIT TRANSACTION;
END;

EXEC CreateAddress
    @StreetOne = '123 Main St',
    @StreetTwo = 'Apt 1',
    @City = 'Anytown',
    @State = 'CA',
    @Zip = '12345',
    @Country = 'USA';
GO


DROP PROCEDURE IF EXISTS ReadAddresses;
GO

CREATE PROCEDURE ReadAddresses
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [Country]
    FROM dbo.[Address]
    ORDER BY [StreetOne] ASC, [City] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadAddresses;
GO


DROP PROCEDURE IF EXISTS ReadAddressById;
GO

CREATE PROCEDURE ReadAddressById
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
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [Country]
    FROM dbo.[Address]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadAddressById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadAddressByPublicId;
GO

CREATE PROCEDURE ReadAddressByPublicId
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
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [Country]
    FROM dbo.[Address]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadAddressByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadAddressByStreetOneAndCity;
GO

CREATE PROCEDURE ReadAddressByStreetOneAndCity
(
    @StreetOne NVARCHAR(255),
    @City NVARCHAR(255)
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
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [Country]
    FROM dbo.[Address]
    WHERE [StreetOne] = @StreetOne AND [City] = @City;

    COMMIT TRANSACTION;
END;

EXEC ReadAddressByStreetOneAndCity
    @StreetOne = '123 Main St',
    @City = 'Anytown';
GO


DROP PROCEDURE IF EXISTS UpdateAddressById;
GO

CREATE PROCEDURE UpdateAddressById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @StreetOne NVARCHAR(255),
    @StreetTwo NVARCHAR(255),
    @City NVARCHAR(255),
    @State NVARCHAR(2),
    @Zip NVARCHAR(5),
    @Country NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Address]
    SET
        [ModifiedDatetime] = @Now,
        [StreetOne] = @StreetOne,
        [StreetTwo] = @StreetTwo,
        [City] = @City,
        [State] = @State,
        [Zip] = @Zip,
        [Country] = @Country
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[StreetOne],
        INSERTED.[StreetTwo],
        INSERTED.[City],
        INSERTED.[State],
        INSERTED.[Zip],
        INSERTED.[Country]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateAddressById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @StreetOne = '123 Main St',
    @StreetTwo = 'Apt 1',
    @City = 'Anytown',
    @State = 'CA',
    @Zip = '12345',
    @Country = 'USA';
GO


DROP PROCEDURE IF EXISTS DeleteAddressById;
GO

CREATE PROCEDURE DeleteAddressById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Address]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[StreetOne],
        DELETED.[StreetTwo],
        DELETED.[City],
        DELETED.[State],
        DELETED.[Zip],
        DELETED.[Country]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteAddressById
    @Id = 1;
GO
