GO

IF OBJECT_ID('qbo.Customer', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Customer]
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
    [FullyQualifiedName] NVARCHAR(MAX) NULL,
    [Level] INT NULL,
    [ParentRefValue] NVARCHAR(50) NULL,
    [ParentRefName] NVARCHAR(500) NULL,
    [Job] BIT NULL,
    [Active] BIT NULL,
    [PrimaryEmailAddr] NVARCHAR(255) NULL,
    [PrimaryPhone] NVARCHAR(50) NULL,
    [Mobile] NVARCHAR(50) NULL,
    [Fax] NVARCHAR(50) NULL,
    [BillAddrId] BIGINT NULL,
    [ShipAddrId] BIGINT NULL,
    [Balance] DECIMAL(18,2) NULL,
    [BalanceWithJobs] DECIMAL(18,2) NULL,
    [Taxable] BIT NULL,
    [Notes] NVARCHAR(MAX) NULL,
    [PrintOnCheckName] NVARCHAR(500) NULL
);
END
GO

IF OBJECT_ID('qbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboCustomer_QboId' AND object_id = OBJECT_ID('qbo.Customer'))
BEGIN
CREATE INDEX IX_QboCustomer_QboId ON [qbo].[Customer] ([QboId]);
END
GO

IF OBJECT_ID('qbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboCustomer_RealmId' AND object_id = OBJECT_ID('qbo.Customer'))
BEGIN
CREATE INDEX IX_QboCustomer_RealmId ON [qbo].[Customer] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboCustomer_ParentRefValue' AND object_id = OBJECT_ID('qbo.Customer'))
BEGIN
CREATE INDEX IX_QboCustomer_ParentRefValue ON [qbo].[Customer] ([ParentRefValue]);
END
GO

IF OBJECT_ID('qbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboCustomer_Job' AND object_id = OBJECT_ID('qbo.Customer'))
BEGIN
CREATE INDEX IX_QboCustomer_Job ON [qbo].[Customer] ([Job]);
END
GO

IF OBJECT_ID('qbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboCustomer_BillAddrId' AND object_id = OBJECT_ID('qbo.Customer'))
BEGIN
CREATE INDEX IX_QboCustomer_BillAddrId ON [qbo].[Customer] ([BillAddrId]);
END
GO

IF OBJECT_ID('qbo.Customer', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboCustomer_ShipAddrId' AND object_id = OBJECT_ID('qbo.Customer'))
BEGIN
CREATE INDEX IX_QboCustomer_ShipAddrId ON [qbo].[Customer] ([ShipAddrId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateQboCustomer
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
    @FullyQualifiedName NVARCHAR(MAX),
    @Level INT,
    @ParentRefValue NVARCHAR(50),
    @ParentRefName NVARCHAR(500),
    @Job BIT,
    @Active BIT,
    @PrimaryEmailAddr NVARCHAR(255),
    @PrimaryPhone NVARCHAR(50),
    @Mobile NVARCHAR(50),
    @Fax NVARCHAR(50),
    @BillAddrId BIGINT,
    @ShipAddrId BIGINT,
    @Balance DECIMAL(18,2),
    @BalanceWithJobs DECIMAL(18,2),
    @Taxable BIT,
    @Notes NVARCHAR(MAX),
    @PrintOnCheckName NVARCHAR(500)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Customer] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [DisplayName], [Title], [GivenName], [MiddleName], [FamilyName], [Suffix],
        [CompanyName], [FullyQualifiedName], [Level], [ParentRefValue], [ParentRefName],
        [Job], [Active], [PrimaryEmailAddr], [PrimaryPhone], [Mobile], [Fax],
        [BillAddrId], [ShipAddrId],
        [Balance], [BalanceWithJobs], [Taxable], [Notes], [PrintOnCheckName]
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
        INSERTED.[FullyQualifiedName],
        INSERTED.[Level],
        INSERTED.[ParentRefValue],
        INSERTED.[ParentRefName],
        INSERTED.[Job],
        INSERTED.[Active],
        INSERTED.[PrimaryEmailAddr],
        INSERTED.[PrimaryPhone],
        INSERTED.[Mobile],
        INSERTED.[Fax],
        INSERTED.[BillAddrId],
        INSERTED.[ShipAddrId],
        INSERTED.[Balance],
        INSERTED.[BalanceWithJobs],
        INSERTED.[Taxable],
        INSERTED.[Notes],
        INSERTED.[PrintOnCheckName]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @DisplayName, @Title, @GivenName, @MiddleName, @FamilyName, @Suffix,
        @CompanyName, @FullyQualifiedName, @Level, @ParentRefValue, @ParentRefName,
        @Job, @Active, @PrimaryEmailAddr, @PrimaryPhone, @Mobile, @Fax,
        @BillAddrId, @ShipAddrId,
        @Balance, @BalanceWithJobs, @Taxable, @Notes, @PrintOnCheckName
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboCustomers
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
        [FullyQualifiedName],
        [Level],
        [ParentRefValue],
        [ParentRefName],
        [Job],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [ShipAddrId],
        [Balance],
        [BalanceWithJobs],
        [Taxable],
        [Notes],
        [PrintOnCheckName]
    FROM [qbo].[Customer]
    ORDER BY [DisplayName] ASC;

    COMMIT TRANSACTION;
END;
GO



GO

CREATE OR ALTER PROCEDURE ReadQboCustomersByRealmId
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
        [FullyQualifiedName],
        [Level],
        [ParentRefValue],
        [ParentRefName],
        [Job],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [ShipAddrId],
        [Balance],
        [BalanceWithJobs],
        [Taxable],
        [Notes],
        [PrintOnCheckName]
    FROM [qbo].[Customer]
    WHERE [RealmId] = @RealmId
    ORDER BY [DisplayName] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboCustomerById
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
        [FullyQualifiedName],
        [Level],
        [ParentRefValue],
        [ParentRefName],
        [Job],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [ShipAddrId],
        [Balance],
        [BalanceWithJobs],
        [Taxable],
        [Notes],
        [PrintOnCheckName]
    FROM [qbo].[Customer]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboCustomerByQboId
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
        [FullyQualifiedName],
        [Level],
        [ParentRefValue],
        [ParentRefName],
        [Job],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [ShipAddrId],
        [Balance],
        [BalanceWithJobs],
        [Taxable],
        [Notes],
        [PrintOnCheckName]
    FROM [qbo].[Customer]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboCustomerByQboIdAndRealmId
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
        [FullyQualifiedName],
        [Level],
        [ParentRefValue],
        [ParentRefName],
        [Job],
        [Active],
        [PrimaryEmailAddr],
        [PrimaryPhone],
        [Mobile],
        [Fax],
        [BillAddrId],
        [ShipAddrId],
        [Balance],
        [BalanceWithJobs],
        [Taxable],
        [Notes],
        [PrintOnCheckName]
    FROM [qbo].[Customer]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboCustomerByQboId
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
    @FullyQualifiedName NVARCHAR(MAX),
    @Level INT,
    @ParentRefValue NVARCHAR(50),
    @ParentRefName NVARCHAR(500),
    @Job BIT,
    @Active BIT,
    @PrimaryEmailAddr NVARCHAR(255),
    @PrimaryPhone NVARCHAR(50),
    @Mobile NVARCHAR(50),
    @Fax NVARCHAR(50),
    @BillAddrId BIGINT,
    @ShipAddrId BIGINT,
    @Balance DECIMAL(18,2),
    @BalanceWithJobs DECIMAL(18,2),
    @Taxable BIT,
    @Notes NVARCHAR(MAX),
    @PrintOnCheckName NVARCHAR(500)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Customer]
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
        [FullyQualifiedName] = CASE WHEN @FullyQualifiedName IS NULL THEN [FullyQualifiedName] ELSE @FullyQualifiedName END,
        [Level] = CASE WHEN @Level IS NULL THEN [Level] ELSE @Level END,
        [ParentRefValue] = CASE WHEN @ParentRefValue IS NULL THEN [ParentRefValue] ELSE @ParentRefValue END,
        [ParentRefName] = CASE WHEN @ParentRefName IS NULL THEN [ParentRefName] ELSE @ParentRefName END,
        [Job] = CASE WHEN @Job IS NULL THEN [Job] ELSE @Job END,
        [Active] = CASE WHEN @Active IS NULL THEN [Active] ELSE @Active END,
        [PrimaryEmailAddr] = CASE WHEN @PrimaryEmailAddr IS NULL THEN [PrimaryEmailAddr] ELSE @PrimaryEmailAddr END,
        [PrimaryPhone] = CASE WHEN @PrimaryPhone IS NULL THEN [PrimaryPhone] ELSE @PrimaryPhone END,
        [Mobile] = CASE WHEN @Mobile IS NULL THEN [Mobile] ELSE @Mobile END,
        [Fax] = CASE WHEN @Fax IS NULL THEN [Fax] ELSE @Fax END,
        [BillAddrId] = CASE WHEN @BillAddrId IS NULL THEN [BillAddrId] ELSE @BillAddrId END,
        [ShipAddrId] = CASE WHEN @ShipAddrId IS NULL THEN [ShipAddrId] ELSE @ShipAddrId END,
        [Balance] = CASE WHEN @Balance IS NULL THEN [Balance] ELSE @Balance END,
        [BalanceWithJobs] = CASE WHEN @BalanceWithJobs IS NULL THEN [BalanceWithJobs] ELSE @BalanceWithJobs END,
        [Taxable] = CASE WHEN @Taxable IS NULL THEN [Taxable] ELSE @Taxable END,
        [Notes] = CASE WHEN @Notes IS NULL THEN [Notes] ELSE @Notes END,
        [PrintOnCheckName] = CASE WHEN @PrintOnCheckName IS NULL THEN [PrintOnCheckName] ELSE @PrintOnCheckName END
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
        INSERTED.[FullyQualifiedName],
        INSERTED.[Level],
        INSERTED.[ParentRefValue],
        INSERTED.[ParentRefName],
        INSERTED.[Job],
        INSERTED.[Active],
        INSERTED.[PrimaryEmailAddr],
        INSERTED.[PrimaryPhone],
        INSERTED.[Mobile],
        INSERTED.[Fax],
        INSERTED.[BillAddrId],
        INSERTED.[ShipAddrId],
        INSERTED.[Balance],
        INSERTED.[BalanceWithJobs],
        INSERTED.[Taxable],
        INSERTED.[Notes],
        INSERTED.[PrintOnCheckName]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboCustomerByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Customer]
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
        DELETED.[FullyQualifiedName],
        DELETED.[Level],
        DELETED.[ParentRefValue],
        DELETED.[ParentRefName],
        DELETED.[Job],
        DELETED.[Active],
        DELETED.[PrimaryEmailAddr],
        DELETED.[PrimaryPhone],
        DELETED.[Mobile],
        DELETED.[Fax],
        DELETED.[BillAddrId],
        DELETED.[ShipAddrId],
        DELETED.[Balance],
        DELETED.[BalanceWithJobs],
        DELETED.[Taxable],
        DELETED.[Notes],
        DELETED.[PrintOnCheckName]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO
