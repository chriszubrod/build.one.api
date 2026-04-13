GO

IF OBJECT_ID('dbo.VendorAddress', 'U') IS NULL
BEGIN
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
END
GO




GO

CREATE OR ALTER PROCEDURE CreateVendorAddress
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



GO

CREATE OR ALTER PROCEDURE ReadVendorAddresses
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



GO

CREATE OR ALTER PROCEDURE ReadVendorAddressById
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



GO

CREATE OR ALTER PROCEDURE ReadVendorAddressByPublicId
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



GO

CREATE OR ALTER PROCEDURE ReadVendorAddressByVendorId
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



GO

CREATE OR ALTER PROCEDURE ReadVendorAddressByAddressId
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




GO

CREATE OR ALTER PROCEDURE ReadVendorAddressByAddressTypeId
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














GO

CREATE OR ALTER PROCEDURE UpdateVendorAddressById
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



GO

CREATE OR ALTER PROCEDURE DeleteVendorAddressById
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



GO

CREATE OR ALTER PROCEDURE DeleteVendorAddressByVendorId
(
    @VendorId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[VendorAddress]
    WHERE [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;


-- FK constraints
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorAddress_Vendor')
BEGIN
    ALTER TABLE [dbo].[VendorAddress] ADD CONSTRAINT [FK_VendorAddress_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorAddress_Address')
BEGIN
    ALTER TABLE [dbo].[VendorAddress] ADD CONSTRAINT [FK_VendorAddress_Address] FOREIGN KEY ([AddressId]) REFERENCES [dbo].[Address]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorAddress_AddressType')
BEGIN
    ALTER TABLE [dbo].[VendorAddress] ADD CONSTRAINT [FK_VendorAddress_AddressType] FOREIGN KEY ([AddressTypeId]) REFERENCES [dbo].[AddressType]([Id]);
END
GO

