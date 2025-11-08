DROP SCHEMA IF EXISTS [qbo];
GO

CREATE SCHEMA [qbo];
GO

DROP TABLE IF EXISTS [qbo].[Vendor];
GO

CREATE TABLE [qbo].[Vendor]
(
    [Id] NVARCHAR(MAX) NULL,
    [SyncToken] NVARCHAR(MAX) NULL,
    [DisplayName] NVARCHAR(MAX) NULL,
    [Vendor1099] INT NULL,
    [CompanyName] NVARCHAR(MAX) NULL,
    [TaxIdentifier] NVARCHAR(MAX) NULL,
    [PrintOnCheckName] NVARCHAR(MAX) NULL,
    [BillAddrId] NVARCHAR(MAX) NULL
);



DROP PROCEDURE IF EXISTS CreateQboVendor;
GO

CREATE PROCEDURE CreateQboVendor
(
    @Id NVARCHAR(MAX),
    @SyncToken NVARCHAR(MAX),
    @DisplayName NVARCHAR(MAX),
    @Vendor1099 INT,
    @CompanyName NVARCHAR(MAX),
    @TaxIdentifier NVARCHAR(MAX),
    @PrintOnCheckName NVARCHAR(MAX),
    @BillAddrId NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Vendor] ([Id], [SyncToken], [DisplayName], [Vendor1099], [CompanyName], [TaxIdentifier], [PrintOnCheckName], [BillAddrId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[SyncToken],
        INSERTED.[DisplayName],
        INSERTED.[Vendor1099],
        INSERTED.[CompanyName],
        INSERTED.[TaxIdentifier],
        INSERTED.[PrintOnCheckName],
        INSERTED.[BillAddrId]
    VALUES (@Id, @SyncToken, @DisplayName, @Vendor1099, @CompanyName, @TaxIdentifier, @PrintOnCheckName, @BillAddrId);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboVendor @Id = '1', @SyncToken = '1', @DisplayName = 'Test Vendor', @Vendor1099 = 0, @CompanyName = 'Test Company', @TaxIdentifier = '1234567890', @PrintOnCheckName = 'Test Print On Check Name', @BillAddrId = '1';



DROP PROCEDURE IF EXISTS ReadQboVendors;
GO

CREATE PROCEDURE ReadQboVendors
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [SyncToken],
        [DisplayName],
        [Vendor1099],
        [CompanyName],
        [TaxIdentifier],
        [PrintOnCheckName],
        [BillAddrId]
    FROM [qbo].[Vendor]
    ORDER BY [DisplayName] ASC;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboVendors;



DROP PROCEDURE IF EXISTS ReadQboVendorById;
GO

CREATE PROCEDURE ReadQboVendorById
(
    @Id NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [SyncToken],
        [DisplayName],
        [Vendor1099],
        [CompanyName],
        [TaxIdentifier],
        [PrintOnCheckName],
        [BillAddrId]
    FROM [qbo].[Vendor]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboVendorById @Id = '1';



DROP PROCEDURE IF EXISTS ReadQboVendorBySyncToken;
GO

CREATE PROCEDURE ReadQboVendorBySyncToken
(
    @SyncToken NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [SyncToken],
        [DisplayName],
        [Vendor1099],
        [CompanyName],
        [TaxIdentifier],
        [PrintOnCheckName],
        [BillAddrId]
    FROM [qbo].[Vendor]
    WHERE [SyncToken] = @SyncToken;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboVendorBySyncToken @SyncToken = '1';



DROP PROCEDURE IF EXISTS ReadQboVendorByDisplayName;
GO

CREATE PROCEDURE ReadQboVendorByDisplayName
(
    @DisplayName NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [SyncToken],
        [DisplayName],
        [Vendor1099],
        [CompanyName],
        [TaxIdentifier],
        [PrintOnCheckName],
        [BillAddrId]
    FROM [qbo].[Vendor]
    WHERE [DisplayName] = @DisplayName;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboVendorByDisplayName @DisplayName = 'Test Vendor';



DROP PROCEDURE IF EXISTS ReadQboVendorByCompanyName;
GO

CREATE PROCEDURE ReadQboVendorByCompanyName
(
    @CompanyName NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [SyncToken],
        [DisplayName],
        [Vendor1099],
        [CompanyName],
        [TaxIdentifier],
        [PrintOnCheckName],
        [BillAddrId]
    FROM [qbo].[Vendor]
    WHERE [CompanyName] = @CompanyName;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboVendorByCompanyName @CompanyName = 'Test Company';



DROP PROCEDURE IF EXISTS ReadQboVendorByTaxIdentifier;
GO

CREATE PROCEDURE ReadQboVendorByTaxIdentifier
(
    @TaxIdentifier NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [SyncToken],
        [DisplayName],
        [Vendor1099],
        [CompanyName],
        [TaxIdentifier],
        [PrintOnCheckName],
        [BillAddrId]
    FROM [qbo].[Vendor]
    WHERE [TaxIdentifier] = @TaxIdentifier;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboVendorByTaxIdentifier @TaxIdentifier = '1234567890';



DROP PROCEDURE IF EXISTS UpdateQboVendorById;
GO

CREATE PROCEDURE UpdateQboVendorById
(
    @Id NVARCHAR(MAX),
    @SyncToken NVARCHAR(MAX),
    @DisplayName NVARCHAR(MAX),
    @Vendor1099 INT,
    @CompanyName NVARCHAR(MAX),
    @TaxIdentifier NVARCHAR(MAX),
    @PrintOnCheckName NVARCHAR(MAX),
    @BillAddrId NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE [qbo].[Vendor]
    SET [Id] = @Id,
        [SyncToken] = @SyncToken,
        [DisplayName] = @DisplayName,
        [Vendor1099] = @Vendor1099,
        [CompanyName] = @CompanyName,
        [TaxIdentifier] = @TaxIdentifier,
        [PrintOnCheckName] = @PrintOnCheckName,
        [BillAddrId] = @BillAddrId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[SyncToken],
        INSERTED.[DisplayName],
        INSERTED.[Vendor1099],
        INSERTED.[CompanyName],
        INSERTED.[TaxIdentifier],
        INSERTED.[PrintOnCheckName],
        INSERTED.[BillAddrId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateVendorById @Id = '1', @SyncToken = '1', @DisplayName = 'Test Vendor', @Vendor1099 = 0, @CompanyName = 'Test Company', @TaxIdentifier = '1234567890', @PrintOnCheckName = 'Test Print On Check Name', @BillAddrId = '1';



DROP PROCEDURE IF EXISTS DeleteQboVendorById;
GO

CREATE PROCEDURE DeleteQboVendorById
(
    @Id NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Vendor]
    OUTPUT
        DELETED.[Id],
        DELETED.[SyncToken],
        DELETED.[DisplayName],
        DELETED.[Vendor1099],
        DELETED.[CompanyName],
        DELETED.[TaxIdentifier],
        DELETED.[PrintOnCheckName],
        DELETED.[BillAddrId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteVendorById @Id = '1';
