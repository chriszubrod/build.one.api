DROP TABLE IF EXISTS [dbo].[AddressType];
GO

CREATE TABLE [dbo].[AddressType]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(MAX) NOT NULL,
    [Description] NVARCHAR(MAX) NOT NULL,
    [DisplayOrder] INT
);
GO




DROP PROCEDURE IF EXISTS CreateAddressType;
GO

CREATE PROCEDURE CreateAddressType
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DisplayOrder INT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[AddressType] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [DisplayOrder])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DisplayOrder]
    VALUES (@Now, @Now, @Name, @Description, @DisplayOrder);

    COMMIT TRANSACTION;
END;

EXEC CreateAddressType
    @Name = 'Shipping',
    @Description = 'Shipping address',
    @DisplayOrder = 3;
GO


DROP PROCEDURE IF EXISTS ReadAddressTypes;
GO

CREATE PROCEDURE ReadAddressTypes
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
        [Description],
        [DisplayOrder]
    FROM dbo.[AddressType]
    ORDER BY [DisplayOrder] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadAddressTypes;
GO


DROP PROCEDURE IF EXISTS ReadAddressTypeById;
GO

CREATE PROCEDURE ReadAddressTypeById
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
        [Description],
        [DisplayOrder]
    FROM dbo.[AddressType]
    WHERE [Id] = @Id
    ORDER BY [DisplayOrder] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadAddressTypeById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadAddressTypeByPublicId;
GO

CREATE PROCEDURE ReadAddressTypeByPublicId
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
        [Description],
        [DisplayOrder]
    FROM dbo.[AddressType]
    WHERE [PublicId] = @PublicId
    ORDER BY [DisplayOrder] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadAddressTypeByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadAddressTypeByName;
GO

CREATE PROCEDURE ReadAddressTypeByName
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
        [Description],
        [DisplayOrder]
    FROM dbo.[AddressType]
    WHERE [Name] = @Name
    ORDER BY [DisplayOrder] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadAddressTypeName
    @Name = 'Mailing';
GO


DROP PROCEDURE IF EXISTS UpdateAddressTypeById;
GO

CREATE PROCEDURE UpdateAddressTypeById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DisplayOrder INT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[AddressType]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [DisplayOrder] = @DisplayOrder
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DisplayOrder]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateAddressTypeById
    @Id = 3,
    @RowVersion = 0x0000000000021B56,
    @Name = 'Mailing',
    @Description = 'Mailing address',
    @DisplayOrder = 3;
GO

EXEC ReadAddressTypes;


DROP PROCEDURE IF EXISTS DeleteAddressTypeById;
GO

CREATE PROCEDURE DeleteAddressTypeById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[AddressType]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[DisplayOrder]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteAddressTypeById
    @Id = 1;
GO
