DROP TABLE IF EXISTS dbo.[VendorAddress];
GO

CREATE TABLE [dbo].[VendorAddress]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NULL,
    [AddressId] BIGINT NULL,
    [AddressTypeId] BIGINT NULL
);
GO




DROP PROCEDURE IF EXISTS CreateVendorAddress;
GO

CREATE PROCEDURE CreateVendorAddress
(
    @VendorId BIGINT,
    @AddressId BIGINT,
    @AddressTypeId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorAddress] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [AddressId], [AddressTypeId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[AddressId],
        INSERTED.[AddressTypeId]
    VALUES (@Now, @Now, @VendorId, @AddressId, @AddressTypeId);

    COMMIT TRANSACTION;
END;

EXEC CreateVendorAddress
    @VendorId = 1,
    @AddressId = 1,
    @AddressTypeId = 1;
GO


DROP PROCEDURE IF EXISTS ReadVendorAddresses;
GO

CREATE PROCEDURE ReadVendorAddresses
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[VendorAddress]
    ORDER BY [VendorId] ASC, [AddressId] ASC, [AddressTypeId] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorAddresses;
GO


DROP PROCEDURE IF EXISTS ReadVendorAddressById;
GO

CREATE PROCEDURE ReadVendorAddressById
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
        [VendorId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[VendorAddress]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorAddressById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadVendorAddressByPublicId;
GO

CREATE PROCEDURE ReadVendorAddressByPublicId
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
        [VendorId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[VendorAddress]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorAddressByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadVendorAddressByVendorId;
GO

CREATE PROCEDURE ReadVendorAddressByVendorId
(
    @VendorId BIGINT
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
        [VendorId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[VendorAddress]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorAddressByVendorId
    @VendorId = 1;
GO


DROP PROCEDURE IF EXISTS ReadVendorAddressByAddressId;
GO

CREATE PROCEDURE ReadVendorAddressByAddressId
(
    @AddressId BIGINT
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
        [VendorId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[VendorAddress]
    WHERE [AddressId] = @AddressId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorAddressByAddressId
    @AddressId = 1;
GO



DROP PROCEDURE IF EXISTS ReadVendorAddressByAddressTypeId;
GO

CREATE PROCEDURE ReadVendorAddressByAddressTypeId
(
    @AddressTypeId BIGINT
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
        [VendorId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[VendorAddress]
    WHERE [AddressTypeId] = @AddressTypeId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorAddressByAddressTypeId
    @AddressTypeId = 1;
GO













DROP PROCEDURE IF EXISTS UpdateVendorAddressById;
GO

CREATE PROCEDURE UpdateVendorAddressById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @AddressId BIGINT,
    @AddressTypeId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorAddress]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [AddressId] = @AddressId,
        [AddressTypeId] = @AddressTypeId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[AddressId],
        INSERTED.[AddressTypeId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateVendorAddressById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @VendorId = 1,
    @AddressId = 1,
    @AddressTypeId = 1;
GO


DROP PROCEDURE IF EXISTS DeleteVendorAddressById;
GO

CREATE PROCEDURE DeleteVendorAddressById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[VendorAddress]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[AddressId],
        DELETED.[AddressTypeId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteVendorAddressById
    @Id = 1;
GO
