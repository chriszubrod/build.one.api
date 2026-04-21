GO

IF OBJECT_ID('qbo.CompanyInfo', 'U') IS NULL
BEGIN
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
END
GO



GO

CREATE OR ALTER PROCEDURE CreateQboCompanyInfo
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




GO

CREATE OR ALTER PROCEDURE ReadQboCompanyInfos
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




GO

CREATE OR ALTER PROCEDURE ReadQboCompanyInfoByQboId
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




GO

CREATE OR ALTER PROCEDURE ReadQboCompanyInfoById
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




GO

CREATE OR ALTER PROCEDURE ReadQboCompanyInfoByRealmId
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




GO

CREATE OR ALTER PROCEDURE UpdateQboCompanyInfoByQboId
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
        [QboId] = CASE WHEN @QboId IS NULL THEN [QboId] ELSE @QboId END,
        [SyncToken] = CASE WHEN @SyncToken IS NULL THEN [SyncToken] ELSE @SyncToken END,
        [RealmId] = CASE WHEN @RealmId IS NULL THEN [RealmId] ELSE @RealmId END,
        [CompanyName] = CASE WHEN @CompanyName IS NULL THEN [CompanyName] ELSE @CompanyName END,
        [LegalName] = CASE WHEN @LegalName IS NULL THEN [LegalName] ELSE @LegalName END,
        [CompanyAddrId] = CASE WHEN @CompanyAddrId IS NULL THEN [CompanyAddrId] ELSE @CompanyAddrId END,
        [LegalAddrId] = CASE WHEN @LegalAddrId IS NULL THEN [LegalAddrId] ELSE @LegalAddrId END,
        [CustomerCommunicationAddrId] = CASE WHEN @CustomerCommunicationAddrId IS NULL THEN [CustomerCommunicationAddrId] ELSE @CustomerCommunicationAddrId END,
        [TaxPayerId] = CASE WHEN @TaxPayerId IS NULL THEN [TaxPayerId] ELSE @TaxPayerId END,
        [FiscalYearStartMonth] = CASE WHEN @FiscalYearStartMonth IS NULL THEN [FiscalYearStartMonth] ELSE @FiscalYearStartMonth END,
        [Country] = CASE WHEN @Country IS NULL THEN [Country] ELSE @Country END,
        [Email] = CASE WHEN @Email IS NULL THEN [Email] ELSE @Email END,
        [WebAddr] = CASE WHEN @WebAddr IS NULL THEN [WebAddr] ELSE @WebAddr END,
        [CurrencyRef] = CASE WHEN @CurrencyRef IS NULL THEN [CurrencyRef] ELSE @CurrencyRef END
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


GO

CREATE OR ALTER PROCEDURE DeleteQboCompanyInfoByQboId
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

