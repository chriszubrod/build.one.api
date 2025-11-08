CREATE TABLE [dbo].[Vendor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Abbreviation] NVARCHAR(255) NOT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateVendor;
GO

CREATE PROCEDURE CreateVendor
(
    @Name NVARCHAR(50),
    @Abbreviation NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Vendor] ([CreatedDatetime], [ModifiedDatetime], [Name], [Abbreviation])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation]
    VALUES (@Now, @Now, @Name, @Abbreviation);

    COMMIT TRANSACTION;
END;

EXEC CreateVendor
    @Name = 'Acme Supply Co.',
    @Abbreviation = 'ACME';
GO


DROP PROCEDURE IF EXISTS ReadVendors;
GO

CREATE PROCEDURE ReadVendors
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
        [Abbreviation]
    FROM dbo.[Vendor]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadVendors;
GO


DROP PROCEDURE IF EXISTS ReadVendorById;
GO

CREATE PROCEDURE ReadVendorById
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
        [Abbreviation]
    FROM dbo.[Vendor]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorById
    @Id = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadVendorByPublicId;
GO

CREATE PROCEDURE ReadVendorByPublicId
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
        [Abbreviation]
    FROM dbo.[Vendor]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadVendorByName;
GO

CREATE PROCEDURE ReadVendorByName
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
        [Abbreviation]
    FROM dbo.[Vendor]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;

EXEC ReadVendorByName
    @Name = 'Acme Supply Co.';
GO


DROP PROCEDURE IF EXISTS UpdateVendorById;
GO

CREATE PROCEDURE UpdateVendorById
(
    @Id UNIQUEIDENTIFIER,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Abbreviation NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Vendor]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Abbreviation] = @Abbreviation
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Abbreviation]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateVendorById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @Name = 'Acme Supply Co. Updated',
    @Abbreviation = 'ACME-UPD';
GO


DROP PROCEDURE IF EXISTS DeleteVendorById;
GO

CREATE PROCEDURE DeleteVendorById
(
    @Id UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Vendor]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Abbreviation]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteVendorById
    @Id = '00000000-0000-0000-0000-000000000000';
GO
