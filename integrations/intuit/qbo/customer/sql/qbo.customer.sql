DROP TABLE IF EXISTS [qbo].[Customer];
GO

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
GO

CREATE INDEX IX_QboCustomer_QboId ON [qbo].[Customer] ([QboId]);
GO

CREATE INDEX IX_QboCustomer_RealmId ON [qbo].[Customer] ([RealmId]);
GO

CREATE INDEX IX_QboCustomer_ParentRefValue ON [qbo].[Customer] ([ParentRefValue]);
GO

CREATE INDEX IX_QboCustomer_Job ON [qbo].[Customer] ([Job]);
GO

CREATE INDEX IX_QboCustomer_BillAddrId ON [qbo].[Customer] ([BillAddrId]);
GO

CREATE INDEX IX_QboCustomer_ShipAddrId ON [qbo].[Customer] ([ShipAddrId]);
GO


DROP PROCEDURE IF EXISTS CreateQboCustomer;
GO

CREATE PROCEDURE CreateQboCustomer
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


DROP PROCEDURE IF EXISTS ReadQboCustomers;
GO

CREATE PROCEDURE ReadQboCustomers
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

EXEC ReadQboCustomers;
GO


DROP PROCEDURE IF EXISTS ReadQboCustomersByRealmId;
GO

CREATE PROCEDURE ReadQboCustomersByRealmId
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


DROP PROCEDURE IF EXISTS ReadQboCustomerById;
GO

CREATE PROCEDURE ReadQboCustomerById
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


DROP PROCEDURE IF EXISTS ReadQboCustomerByQboId;
GO

CREATE PROCEDURE ReadQboCustomerByQboId
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


DROP PROCEDURE IF EXISTS ReadQboCustomerByQboIdAndRealmId;
GO

CREATE PROCEDURE ReadQboCustomerByQboIdAndRealmId
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


DROP PROCEDURE IF EXISTS UpdateQboCustomerByQboId;
GO

CREATE PROCEDURE UpdateQboCustomerByQboId
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
        [SyncToken] = @SyncToken,
        [RealmId] = @RealmId,
        [DisplayName] = @DisplayName,
        [Title] = @Title,
        [GivenName] = @GivenName,
        [MiddleName] = @MiddleName,
        [FamilyName] = @FamilyName,
        [Suffix] = @Suffix,
        [CompanyName] = @CompanyName,
        [FullyQualifiedName] = @FullyQualifiedName,
        [Level] = @Level,
        [ParentRefValue] = @ParentRefValue,
        [ParentRefName] = @ParentRefName,
        [Job] = @Job,
        [Active] = @Active,
        [PrimaryEmailAddr] = @PrimaryEmailAddr,
        [PrimaryPhone] = @PrimaryPhone,
        [Mobile] = @Mobile,
        [Fax] = @Fax,
        [BillAddrId] = @BillAddrId,
        [ShipAddrId] = @ShipAddrId,
        [Balance] = @Balance,
        [BalanceWithJobs] = @BalanceWithJobs,
        [Taxable] = @Taxable,
        [Notes] = @Notes,
        [PrintOnCheckName] = @PrintOnCheckName
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


DROP PROCEDURE IF EXISTS DeleteQboCustomerByQboId;
GO

CREATE PROCEDURE DeleteQboCustomerByQboId
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
