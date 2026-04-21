GO

IF OBJECT_ID('qbo.Vendor', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Vendor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [DisplayName] NVARCHAR(500) NULL,
    [Title] NVARCHAR(16) NULL,
    [GivenName] NVARCHAR(100) NULL,
    [MiddleName] NVARCHAR(100) NULL,
    [FamilyName] NVARCHAR(100) NULL,
    [Suffix] NVARCHAR(16) NULL,
    [CompanyName] NVARCHAR(500) NULL,
    [PrintOnCheckName] NVARCHAR(500) NULL,
    [TaxIdentifier] NVARCHAR(50) NULL,
    [Vendor1099] BIT NULL,
    [Active] BIT NULL,
    [PrimaryEmailAddr] NVARCHAR(255) NULL,
    [PrimaryPhone] NVARCHAR(50) NULL,
    [Mobile] NVARCHAR(50) NULL,
    [Fax] NVARCHAR(50) NULL,
    [BillAddrId] BIGINT NULL,
    [Balance] DECIMAL(18,2) NULL,
    [AcctNum] NVARCHAR(50) NULL,
    [WebAddr] NVARCHAR(500) NULL
);
END
GO

IF OBJECT_ID('qbo.Vendor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboVendor_QboId' AND object_id = OBJECT_ID('qbo.Vendor'))
BEGIN
CREATE INDEX IX_QboVendor_QboId ON [qbo].[Vendor] ([QboId]);
END
GO

IF OBJECT_ID('qbo.Vendor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboVendor_RealmId' AND object_id = OBJECT_ID('qbo.Vendor'))
BEGIN
CREATE INDEX IX_QboVendor_RealmId ON [qbo].[Vendor] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Vendor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboVendor_BillAddrId' AND object_id = OBJECT_ID('qbo.Vendor'))
BEGIN
CREATE INDEX IX_QboVendor_BillAddrId ON [qbo].[Vendor] ([BillAddrId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateQboVendor
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @DisplayName NVARCHAR(500),
    @Title NVARCHAR(16),
    @GivenName NVARCHAR(100),
    @MiddleName NVARCHAR(100),
    @FamilyName NVARCHAR(100),
    @Suffix NVARCHAR(16),
    @CompanyName NVARCHAR(500),
    @PrintOnCheckName NVARCHAR(500),
    @TaxIdentifier NVARCHAR(50),
    @Vendor1099 BIT,
    @Active BIT,
    @PrimaryEmailAddr NVARCHAR(255),
    @PrimaryPhone NVARCHAR(50),
    @Mobile NVARCHAR(50),
    @Fax NVARCHAR(50),
    @BillAddrId BIGINT,
    @Balance DECIMAL(18,2),
    @AcctNum NVARCHAR(50),
    @WebAddr NVARCHAR(500)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Vendor] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [DisplayName], [Title], [GivenName], [MiddleName], [FamilyName], [Suffix],
        [CompanyName], [PrintOnCheckName], [TaxIdentifier], [Vendor1099], [Active],
        [PrimaryEmailAddr], [PrimaryPhone], [Mobile], [Fax],
        [BillAddrId], [Balance], [AcctNum], [WebAddr]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[DisplayName],
        INSERTED.[Title],
        INSERTED.[GivenName],
        INSERTED.[MiddleName],
        INSERTED.[FamilyName],
        INSERTED.[Suffix],
        INSERTED.[CompanyName],
        INSERTED.[PrintOnCheckName],
        INSERTED.[TaxIdentifier],
        INSERTED.[Vendor1099],
        INSERTED.[Active],
        INSERTED.[PrimaryEmailAddr],
        INSERTED.[PrimaryPhone],
        INSERTED.[Mobile],
        INSERTED.[Fax],
        INSERTED.[BillAddrId],
        INSERTED.[Balance],
        INSERTED.[AcctNum],
        INSERTED.[WebAddr]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @DisplayName, @Title, @GivenName, @MiddleName, @FamilyName, @Suffix,
        @CompanyName, @PrintOnCheckName, @TaxIdentifier, @Vendor1099, @Active,
        @PrimaryEmailAddr, @PrimaryPhone, @Mobile, @Fax,
        @BillAddrId, @Balance, @AcctNum, @WebAddr
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboVendors
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
        [SyncToken],
        [RealmId],
        [DisplayName],
        [Title],
        [GivenName],
        [MiddleName],
        [FamilyName],
        [Suffix],
        [CompanyName],
        [PrintOnCheckName],
        [TaxIdentifier],
        [Vendor1099],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [Balance],
        [AcctNum],
        [WebAddr]
    FROM [qbo].[Vendor]
    ORDER BY [DisplayName] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboVendorsByRealmId
(
    @RealmId NVARCHAR(50)
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
        [SyncToken],
        [RealmId],
        [DisplayName],
        [Title],
        [GivenName],
        [MiddleName],
        [FamilyName],
        [Suffix],
        [CompanyName],
        [PrintOnCheckName],
        [TaxIdentifier],
        [Vendor1099],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [Balance],
        [AcctNum],
        [WebAddr]
    FROM [qbo].[Vendor]
    WHERE [RealmId] = @RealmId
    ORDER BY [DisplayName] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboVendorById
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
        [SyncToken],
        [RealmId],
        [DisplayName],
        [Title],
        [GivenName],
        [MiddleName],
        [FamilyName],
        [Suffix],
        [CompanyName],
        [PrintOnCheckName],
        [TaxIdentifier],
        [Vendor1099],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [Balance],
        [AcctNum],
        [WebAddr]
    FROM [qbo].[Vendor]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboVendorByQboId
(
    @QboId NVARCHAR(50)
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
        [SyncToken],
        [RealmId],
        [DisplayName],
        [Title],
        [GivenName],
        [MiddleName],
        [FamilyName],
        [Suffix],
        [CompanyName],
        [PrintOnCheckName],
        [TaxIdentifier],
        [Vendor1099],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [Balance],
        [AcctNum],
        [WebAddr]
    FROM [qbo].[Vendor]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboVendorByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
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
        [SyncToken],
        [RealmId],
        [DisplayName],
        [Title],
        [GivenName],
        [MiddleName],
        [FamilyName],
        [Suffix],
        [CompanyName],
        [PrintOnCheckName],
        [TaxIdentifier],
        [Vendor1099],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [Balance],
        [AcctNum],
        [WebAddr]
    FROM [qbo].[Vendor]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboVendorByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @DisplayName NVARCHAR(500),
    @Title NVARCHAR(16),
    @GivenName NVARCHAR(100),
    @MiddleName NVARCHAR(100),
    @FamilyName NVARCHAR(100),
    @Suffix NVARCHAR(16),
    @CompanyName NVARCHAR(500),
    @PrintOnCheckName NVARCHAR(500),
    @TaxIdentifier NVARCHAR(50),
    @Vendor1099 BIT,
    @Active BIT,
    @PrimaryEmailAddr NVARCHAR(255),
    @PrimaryPhone NVARCHAR(50),
    @Mobile NVARCHAR(50),
    @Fax NVARCHAR(50),
    @BillAddrId BIGINT,
    @Balance DECIMAL(18,2),
    @AcctNum NVARCHAR(50),
    @WebAddr NVARCHAR(500)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Vendor]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = CASE WHEN @SyncToken IS NULL THEN [SyncToken] ELSE @SyncToken END,
        [RealmId] = CASE WHEN @RealmId IS NULL THEN [RealmId] ELSE @RealmId END,
        [DisplayName] = CASE WHEN @DisplayName IS NULL THEN [DisplayName] ELSE @DisplayName END,
        [Title] = CASE WHEN @Title IS NULL THEN [Title] ELSE @Title END,
        [GivenName] = CASE WHEN @GivenName IS NULL THEN [GivenName] ELSE @GivenName END,
        [MiddleName] = CASE WHEN @MiddleName IS NULL THEN [MiddleName] ELSE @MiddleName END,
        [FamilyName] = CASE WHEN @FamilyName IS NULL THEN [FamilyName] ELSE @FamilyName END,
        [Suffix] = CASE WHEN @Suffix IS NULL THEN [Suffix] ELSE @Suffix END,
        [CompanyName] = CASE WHEN @CompanyName IS NULL THEN [CompanyName] ELSE @CompanyName END,
        [PrintOnCheckName] = CASE WHEN @PrintOnCheckName IS NULL THEN [PrintOnCheckName] ELSE @PrintOnCheckName END,
        [TaxIdentifier] = CASE WHEN @TaxIdentifier IS NULL THEN [TaxIdentifier] ELSE @TaxIdentifier END,
        [Vendor1099] = CASE WHEN @Vendor1099 IS NULL THEN [Vendor1099] ELSE @Vendor1099 END,
        [Active] = CASE WHEN @Active IS NULL THEN [Active] ELSE @Active END,
        [PrimaryEmailAddr] = CASE WHEN @PrimaryEmailAddr IS NULL THEN [PrimaryEmailAddr] ELSE @PrimaryEmailAddr END,
        [PrimaryPhone] = CASE WHEN @PrimaryPhone IS NULL THEN [PrimaryPhone] ELSE @PrimaryPhone END,
        [Mobile] = CASE WHEN @Mobile IS NULL THEN [Mobile] ELSE @Mobile END,
        [Fax] = CASE WHEN @Fax IS NULL THEN [Fax] ELSE @Fax END,
        [BillAddrId] = CASE WHEN @BillAddrId IS NULL THEN [BillAddrId] ELSE @BillAddrId END,
        [Balance] = CASE WHEN @Balance IS NULL THEN [Balance] ELSE @Balance END,
        [AcctNum] = CASE WHEN @AcctNum IS NULL THEN [AcctNum] ELSE @AcctNum END,
        [WebAddr] = CASE WHEN @WebAddr IS NULL THEN [WebAddr] ELSE @WebAddr END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[DisplayName],
        INSERTED.[Title],
        INSERTED.[GivenName],
        INSERTED.[MiddleName],
        INSERTED.[FamilyName],
        INSERTED.[Suffix],
        INSERTED.[CompanyName],
        INSERTED.[PrintOnCheckName],
        INSERTED.[TaxIdentifier],
        INSERTED.[Vendor1099],
        INSERTED.[Active],
        INSERTED.[PrimaryEmailAddr],
        INSERTED.[PrimaryPhone],
        INSERTED.[Mobile],
        INSERTED.[Fax],
        INSERTED.[BillAddrId],
        INSERTED.[Balance],
        INSERTED.[AcctNum],
        INSERTED.[WebAddr]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboVendorByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Vendor]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[DisplayName],
        DELETED.[Title],
        DELETED.[GivenName],
        DELETED.[MiddleName],
        DELETED.[FamilyName],
        DELETED.[Suffix],
        DELETED.[CompanyName],
        DELETED.[PrintOnCheckName],
        DELETED.[TaxIdentifier],
        DELETED.[Vendor1099],
        DELETED.[Active],
        DELETED.[PrimaryEmailAddr],
        DELETED.[PrimaryPhone],
        DELETED.[Mobile],
        DELETED.[Fax],
        DELETED.[BillAddrId],
        DELETED.[Balance],
        DELETED.[AcctNum],
        DELETED.[WebAddr]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO
