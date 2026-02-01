-- PhysicalAddressAddress Mapping Table
-- Links QBO PhysicalAddress records to system Address records (1:1 relationship)

GO

IF OBJECT_ID('qbo.PhysicalAddressAddress', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[PhysicalAddressAddress]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [AddressId] BIGINT NOT NULL,
    [QboPhysicalAddressId] BIGINT NOT NULL,
    CONSTRAINT [FK_PhysicalAddressAddress_Address] FOREIGN KEY ([AddressId]) REFERENCES [dbo].[Address]([Id]),
    CONSTRAINT [FK_PhysicalAddressAddress_QboPhysicalAddress] FOREIGN KEY ([QboPhysicalAddressId]) REFERENCES [qbo].[PhysicalAddress]([Id]),
    CONSTRAINT [UQ_PhysicalAddressAddress_AddressId] UNIQUE ([AddressId]),
    CONSTRAINT [UQ_PhysicalAddressAddress_QboPhysicalAddressId] UNIQUE ([QboPhysicalAddressId])
);
END
GO

IF OBJECT_ID('qbo.PhysicalAddressAddress', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_PhysicalAddressAddress_AddressId' AND object_id = OBJECT_ID('qbo.PhysicalAddressAddress'))
BEGIN
CREATE INDEX [IX_PhysicalAddressAddress_AddressId] ON [qbo].[PhysicalAddressAddress]([AddressId]);
END
GO

IF OBJECT_ID('qbo.PhysicalAddressAddress', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_PhysicalAddressAddress_QboPhysicalAddressId' AND object_id = OBJECT_ID('qbo.PhysicalAddressAddress'))
BEGIN
CREATE INDEX [IX_PhysicalAddressAddress_QboPhysicalAddressId] ON [qbo].[PhysicalAddressAddress]([QboPhysicalAddressId]);
END
GO



-- Create PhysicalAddressAddress
GO

CREATE OR ALTER PROCEDURE CreatePhysicalAddressAddress
(
    @AddressId BIGINT,
    @QboPhysicalAddressId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[PhysicalAddressAddress] ([CreatedDatetime], [ModifiedDatetime], [AddressId], [QboPhysicalAddressId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[AddressId],
        INSERTED.[QboPhysicalAddressId]
    VALUES (@Now, @Now, @AddressId, @QboPhysicalAddressId);

    COMMIT TRANSACTION;
END;
GO



-- Read PhysicalAddressAddress by Id
GO

CREATE OR ALTER PROCEDURE ReadPhysicalAddressAddressById
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
        [AddressId],
        [QboPhysicalAddressId]
    FROM [qbo].[PhysicalAddressAddress]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO



-- Read PhysicalAddressAddress by PublicId
GO

CREATE OR ALTER PROCEDURE ReadPhysicalAddressAddressByPublicId
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
        [AddressId],
        [QboPhysicalAddressId]
    FROM [qbo].[PhysicalAddressAddress]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO



-- Read PhysicalAddressAddress by AddressId
GO

CREATE OR ALTER PROCEDURE ReadPhysicalAddressAddressByAddressId
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
        [AddressId],
        [QboPhysicalAddressId]
    FROM [qbo].[PhysicalAddressAddress]
    WHERE [AddressId] = @AddressId;

    COMMIT TRANSACTION;
END;
GO



-- Read PhysicalAddressAddress by QboPhysicalAddressId
GO

CREATE OR ALTER PROCEDURE ReadPhysicalAddressAddressByQboPhysicalAddressId
(
    @QboPhysicalAddressId BIGINT
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
        [AddressId],
        [QboPhysicalAddressId]
    FROM [qbo].[PhysicalAddressAddress]
    WHERE [QboPhysicalAddressId] = @QboPhysicalAddressId;

    COMMIT TRANSACTION;
END;
GO



-- Update PhysicalAddressAddress by Id
GO

CREATE OR ALTER PROCEDURE UpdatePhysicalAddressAddressById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @AddressId BIGINT,
    @QboPhysicalAddressId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[PhysicalAddressAddress]
    SET [ModifiedDatetime] = @Now,
        [AddressId] = @AddressId,
        [QboPhysicalAddressId] = @QboPhysicalAddressId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[AddressId],
        INSERTED.[QboPhysicalAddressId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO



-- Delete PhysicalAddressAddress by Id
GO

CREATE OR ALTER PROCEDURE DeletePhysicalAddressAddressById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[PhysicalAddressAddress]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[AddressId],
        DELETED.[QboPhysicalAddressId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

