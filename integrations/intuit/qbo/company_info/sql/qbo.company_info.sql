DROP TABLE IF EXISTS [qbo].[CompanyInfo];
GO

CREATE TABLE [qbo].[CompanyInfo]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(MAX) NULL,
    [SyncToken] NVARCHAR(MAX) NULL,
    [RealmId] NVARCHAR(MAX) NULL,
    [CompanyName] NVARCHAR(MAX) NULL,
    [LegalName] NVARCHAR(MAX) NULL,
    [CompanyAddrId] BIGINT NULL,
    [LegalAddrId] BIGINT NULL,
    [CustomerCommunicationAddrId] BIGINT NULL,
    [TaxPayerId] NVARCHAR(MAX) NULL,
    [FiscalYearStartMonth] INT NULL,
    [Country] NVARCHAR(MAX) NULL,
    [Email] NVARCHAR(MAX) NULL,
    [WebAddr] NVARCHAR(MAX) NULL,
    [CurrencyRef] NVARCHAR(MAX) NULL
);
GO



DROP PROCEDURE IF EXISTS CreateQboCompanyInfo;
GO

CREATE PROCEDURE CreateQboCompanyInfo
(
    @QboId NVARCHAR(MAX),
    @SyncToken NVARCHAR(MAX),
    @RealmId NVARCHAR(MAX),
    @CompanyName NVARCHAR(MAX),
    @LegalName NVARCHAR(MAX),
    @CompanyAddrId BIGINT,
    @LegalAddrId BIGINT,
    @CustomerCommunicationAddrId BIGINT,
    @TaxPayerId NVARCHAR(MAX),
    @FiscalYearStartMonth INT,
    @Country NVARCHAR(MAX),
    @Email NVARCHAR(MAX),
    @WebAddr NVARCHAR(MAX),
    @CurrencyRef NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[CompanyInfo] ([CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId], [CompanyName], [LegalName], [CompanyAddrId], [LegalAddrId], [CustomerCommunicationAddrId], [TaxPayerId], [FiscalYearStartMonth], [Country], [Email], [WebAddr], [CurrencyRef])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[CompanyName],
        INSERTED.[LegalName],
        INSERTED.[CompanyAddrId],
        INSERTED.[LegalAddrId],
        INSERTED.[CustomerCommunicationAddrId],
        INSERTED.[TaxPayerId],
        INSERTED.[FiscalYearStartMonth],
        INSERTED.[Country],
        INSERTED.[Email],
        INSERTED.[WebAddr],
        INSERTED.[CurrencyRef]
    VALUES (@Now, @Now, @QboId, @SyncToken, @RealmId, @CompanyName, @LegalName, @CompanyAddrId, @LegalAddrId, @CustomerCommunicationAddrId, @TaxPayerId, @FiscalYearStartMonth, @Country, @Email, @WebAddr, @CurrencyRef);

    COMMIT TRANSACTION;
END;
GO

EXEC CreateQboCompanyInfo
    @QboId = '1',
    @SyncToken = '0',
    @RealmId = '123456789',
    @CompanyName = 'Test Company',
    @LegalName = 'Test Company Legal',
    @CompanyAddrId = NULL,
    @LegalAddrId = NULL,
    @CustomerCommunicationAddrId = NULL,
    @TaxPayerId = '12-3456789',
    @FiscalYearStartMonth = 1,
    @Country = 'USA',
    @Email = 'test@example.com',
    @WebAddr = 'https://example.com',
    @CurrencyRef = 'USD';
GO



DROP PROCEDURE IF EXISTS ReadQboCompanyInfos;
GO

CREATE PROCEDURE ReadQboCompanyInfos
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
        [CompanyName],
        [LegalName],
        [CompanyAddrId],
        [LegalAddrId],
        [CustomerCommunicationAddrId],
        [TaxPayerId],
        [FiscalYearStartMonth],
        [Country],
        [Email],
        [WebAddr],
        [CurrencyRef]
    FROM [qbo].[CompanyInfo]
    ORDER BY [CompanyName] ASC;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboCompanyInfos;
GO



DROP PROCEDURE IF EXISTS ReadQboCompanyInfoByQboId;
GO

CREATE PROCEDURE ReadQboCompanyInfoByQboId
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
        [SyncToken],
        [RealmId],
        [CompanyName],
        [LegalName],
        [CompanyAddrId],
        [LegalAddrId],
        [CustomerCommunicationAddrId],
        [TaxPayerId],
        [FiscalYearStartMonth],
        [Country],
        [Email],
        [WebAddr],
        [CurrencyRef]
    FROM [qbo].[CompanyInfo]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboCompanyInfoByQboId
    @QboId = '1';
GO



DROP PROCEDURE IF EXISTS ReadQboCompanyInfoById;
GO

CREATE PROCEDURE ReadQboCompanyInfoById
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
        [CompanyName],
        [LegalName],
        [CompanyAddrId],
        [LegalAddrId],
        [CustomerCommunicationAddrId],
        [TaxPayerId],
        [FiscalYearStartMonth],
        [Country],
        [Email],
        [WebAddr],
        [CurrencyRef]
    FROM [qbo].[CompanyInfo]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboCompanyInfoById
    @Id = 1;
GO



DROP PROCEDURE IF EXISTS ReadQboCompanyInfoByRealmId;
GO

CREATE PROCEDURE ReadQboCompanyInfoByRealmId
(
    @RealmId NVARCHAR(MAX)
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
        [CompanyName],
        [LegalName],
        [CompanyAddrId],
        [LegalAddrId],
        [CustomerCommunicationAddrId],
        [TaxPayerId],
        [FiscalYearStartMonth],
        [Country],
        [Email],
        [WebAddr],
        [CurrencyRef]
    FROM [qbo].[CompanyInfo]
    WHERE [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboCompanyInfoByRealmId
    @RealmId = '123456789';
GO



DROP PROCEDURE IF EXISTS UpdateQboCompanyInfoByQboId;
GO

CREATE PROCEDURE UpdateQboCompanyInfoByQboId
(
    @QboId NVARCHAR(MAX),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(MAX),
    @RealmId NVARCHAR(MAX),
    @CompanyName NVARCHAR(MAX),
    @LegalName NVARCHAR(MAX),
    @CompanyAddrId BIGINT,
    @LegalAddrId BIGINT,
    @CustomerCommunicationAddrId BIGINT,
    @TaxPayerId NVARCHAR(MAX),
    @FiscalYearStartMonth INT,
    @Country NVARCHAR(MAX),
    @Email NVARCHAR(MAX),
    @WebAddr NVARCHAR(MAX),
    @CurrencyRef NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[CompanyInfo]
    SET [ModifiedDatetime] = @Now,
        [QboId] = @QboId,
        [SyncToken] = @SyncToken,
        [RealmId] = @RealmId,
        [CompanyName] = @CompanyName,
        [LegalName] = @LegalName,
        [CompanyAddrId] = @CompanyAddrId,
        [LegalAddrId] = @LegalAddrId,
        [CustomerCommunicationAddrId] = @CustomerCommunicationAddrId,
        [TaxPayerId] = @TaxPayerId,
        [FiscalYearStartMonth] = @FiscalYearStartMonth,
        [Country] = @Country,
        [Email] = @Email,
        [WebAddr] = @WebAddr,
        [CurrencyRef] = @CurrencyRef
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[CompanyName],
        INSERTED.[LegalName],
        INSERTED.[CompanyAddrId],
        INSERTED.[LegalAddrId],
        INSERTED.[CustomerCommunicationAddrId],
        INSERTED.[TaxPayerId],
        INSERTED.[FiscalYearStartMonth],
        INSERTED.[Country],
        INSERTED.[Email],
        INSERTED.[WebAddr],
        INSERTED.[CurrencyRef]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

EXEC UpdateQboCompanyInfoByQboId
    @QboId = '1',
    @RowVersion = 0x0000000000020B74,
    @SyncToken = '1',
    @RealmId = '123456789',
    @CompanyName = 'Test Company Updated',
    @LegalName = 'Test Company Legal Updated',
    @CompanyAddrId = NULL,
    @LegalAddrId = NULL,
    @CustomerCommunicationAddrId = NULL,
    @TaxPayerId = '12-3456789',
    @FiscalYearStartMonth = 1,
    @Country = 'USA',
    @Email = 'test@example.com',
    @WebAddr = 'https://example.com',
    @CurrencyRef = 'USD';
GO

DROP PROCEDURE IF EXISTS DeleteQboCompanyInfoByQboId;
GO

CREATE PROCEDURE DeleteQboCompanyInfoByQboId
(
    @QboId NVARCHAR(MAX)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[CompanyInfo]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[CompanyName],
        DELETED.[LegalName],
        DELETED.[CompanyAddrId],
        DELETED.[LegalAddrId],
        DELETED.[CustomerCommunicationAddrId],
        DELETED.[TaxPayerId],
        DELETED.[FiscalYearStartMonth],
        DELETED.[Country],
        DELETED.[Email],
        DELETED.[WebAddr],
        DELETED.[CurrencyRef]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO

EXEC DeleteQboCompanyInfoByQboId
    @QboId = '1';
GO
