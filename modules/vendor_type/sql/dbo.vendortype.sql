CREATE TABLE [dbo].[VendorType]
(
    [Id] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Description] NVARCHAR(255) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateVendorType;
GO

CREATE PROCEDURE CreateVendorType
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorType] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description]
    VALUES (@Now, @Now, @Name, @Description);

    COMMIT TRANSACTION;
END;

EXEC CreateVendorType
    @Name = 'Materials Supplier',
    @Description = 'Vendor that supplies construction materials';
GO


DROP PROCEDURE IF EXISTS ReadVendorTypes;
GO

CREATE PROCEDURE ReadVendorTypes
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
        [Description]
    FROM dbo.[VendorType]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorTypes;
GO


DROP PROCEDURE IF EXISTS ReadVendorTypeById;
GO

CREATE PROCEDURE ReadVendorTypeById
(
    @Id UNIQUEIDENTIFIER
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
        [Description]
    FROM dbo.[VendorType]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorTypeById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadVendorTypeByPublicId;
GO

CREATE PROCEDURE ReadVendorTypeByPublicId
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
        [Description]
    FROM dbo.[VendorType]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorTypeByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadVendorTypeByName;
GO

CREATE PROCEDURE ReadVendorTypeByName
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
        [Description]
    FROM dbo.[VendorType]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorTypeByName
    @Name = 'General Vendor';
GO


DROP PROCEDURE IF EXISTS UpdateVendorTypeById;
GO

CREATE PROCEDURE UpdateVendorTypeById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Description NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorType]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateVendorTypeById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @Name = 'Updated Vendor',
    @Description = 'Updated vendor type description';
GO


DROP PROCEDURE IF EXISTS DeleteVendorTypeById;
GO

CREATE PROCEDURE DeleteVendorTypeById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[VendorType]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteVendorTypeById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
