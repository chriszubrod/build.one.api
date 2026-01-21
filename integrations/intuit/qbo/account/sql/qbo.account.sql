DROP TABLE IF EXISTS [qbo].[Account];
GO

CREATE TABLE [qbo].[Account]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [Name] NVARCHAR(100) NULL,
    [AcctNum] NVARCHAR(20) NULL,
    [Description] NVARCHAR(100) NULL,
    [Active] BIT NULL,
    [Classification] NVARCHAR(50) NULL,
    [AccountType] NVARCHAR(100) NULL,
    [AccountSubType] NVARCHAR(100) NULL,
    [FullyQualifiedName] NVARCHAR(500) NULL,
    [SubAccount] BIT NULL,
    [ParentRefValue] NVARCHAR(50) NULL,
    [ParentRefName] NVARCHAR(100) NULL,
    [CurrentBalance] DECIMAL(18,2) NULL,
    [CurrentBalanceWithSubAccounts] DECIMAL(18,2) NULL,
    [CurrencyRefValue] NVARCHAR(10) NULL,
    [CurrencyRefName] NVARCHAR(100) NULL
);
GO

CREATE INDEX IX_QboAccount_QboId ON [qbo].[Account] ([QboId]);
GO

CREATE INDEX IX_QboAccount_RealmId ON [qbo].[Account] ([RealmId]);
GO

CREATE INDEX IX_QboAccount_AccountType ON [qbo].[Account] ([AccountType]);
GO

CREATE INDEX IX_QboAccount_Classification ON [qbo].[Account] ([Classification]);
GO


DROP PROCEDURE IF EXISTS CreateQboAccount;
GO

CREATE PROCEDURE CreateQboAccount
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @Name NVARCHAR(100),
    @AcctNum NVARCHAR(20),
    @Description NVARCHAR(100),
    @Active BIT,
    @Classification NVARCHAR(50),
    @AccountType NVARCHAR(100),
    @AccountSubType NVARCHAR(100),
    @FullyQualifiedName NVARCHAR(500),
    @SubAccount BIT,
    @ParentRefValue NVARCHAR(50),
    @ParentRefName NVARCHAR(100),
    @CurrentBalance DECIMAL(18,2),
    @CurrentBalanceWithSubAccounts DECIMAL(18,2),
    @CurrencyRefValue NVARCHAR(10),
    @CurrencyRefName NVARCHAR(100)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Account] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [Name], [AcctNum], [Description], [Active],
        [Classification], [AccountType], [AccountSubType], [FullyQualifiedName],
        [SubAccount], [ParentRefValue], [ParentRefName],
        [CurrentBalance], [CurrentBalanceWithSubAccounts],
        [CurrencyRefValue], [CurrencyRefName]
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
        INSERTED.[Name],
        INSERTED.[AcctNum],
        INSERTED.[Description],
        INSERTED.[Active],
        INSERTED.[Classification],
        INSERTED.[AccountType],
        INSERTED.[AccountSubType],
        INSERTED.[FullyQualifiedName],
        INSERTED.[SubAccount],
        INSERTED.[ParentRefValue],
        INSERTED.[ParentRefName],
        INSERTED.[CurrentBalance],
        INSERTED.[CurrentBalanceWithSubAccounts],
        INSERTED.[CurrencyRefValue],
        INSERTED.[CurrencyRefName]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @Name, @AcctNum, @Description, @Active,
        @Classification, @AccountType, @AccountSubType, @FullyQualifiedName,
        @SubAccount, @ParentRefValue, @ParentRefName,
        @CurrentBalance, @CurrentBalanceWithSubAccounts,
        @CurrencyRefValue, @CurrencyRefName
    );

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadQboAccounts;
GO

CREATE PROCEDURE ReadQboAccounts
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
        [Name],
        [AcctNum],
        [Description],
        [Active],
        [Classification],
        [AccountType],
        [AccountSubType],
        [FullyQualifiedName],
        [SubAccount],
        [ParentRefValue],
        [ParentRefName],
        [CurrentBalance],
        [CurrentBalanceWithSubAccounts],
        [CurrencyRefValue],
        [CurrencyRefName]
    FROM [qbo].[Account]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO

EXEC ReadQboAccounts;
GO

DROP PROCEDURE IF EXISTS ReadQboAccountsByRealmId;
GO

CREATE PROCEDURE ReadQboAccountsByRealmId
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
        [Name],
        [AcctNum],
        [Description],
        [Active],
        [Classification],
        [AccountType],
        [AccountSubType],
        [FullyQualifiedName],
        [SubAccount],
        [ParentRefValue],
        [ParentRefName],
        [CurrentBalance],
        [CurrentBalanceWithSubAccounts],
        [CurrencyRefValue],
        [CurrencyRefName]
    FROM [qbo].[Account]
    WHERE [RealmId] = @RealmId
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadQboAccountById;
GO

CREATE PROCEDURE ReadQboAccountById
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
        [Name],
        [AcctNum],
        [Description],
        [Active],
        [Classification],
        [AccountType],
        [AccountSubType],
        [FullyQualifiedName],
        [SubAccount],
        [ParentRefValue],
        [ParentRefName],
        [CurrentBalance],
        [CurrentBalanceWithSubAccounts],
        [CurrencyRefValue],
        [CurrencyRefName]
    FROM [qbo].[Account]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadQboAccountByQboId;
GO

CREATE PROCEDURE ReadQboAccountByQboId
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
        [Name],
        [AcctNum],
        [Description],
        [Active],
        [Classification],
        [AccountType],
        [AccountSubType],
        [FullyQualifiedName],
        [SubAccount],
        [ParentRefValue],
        [ParentRefName],
        [CurrentBalance],
        [CurrentBalanceWithSubAccounts],
        [CurrencyRefValue],
        [CurrencyRefName]
    FROM [qbo].[Account]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS ReadQboAccountByQboIdAndRealmId;
GO

CREATE PROCEDURE ReadQboAccountByQboIdAndRealmId
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
        [Name],
        [AcctNum],
        [Description],
        [Active],
        [Classification],
        [AccountType],
        [AccountSubType],
        [FullyQualifiedName],
        [SubAccount],
        [ParentRefValue],
        [ParentRefName],
        [CurrentBalance],
        [CurrentBalanceWithSubAccounts],
        [CurrencyRefValue],
        [CurrencyRefName]
    FROM [qbo].[Account]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS UpdateQboAccountByQboId;
GO

CREATE PROCEDURE UpdateQboAccountByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @Name NVARCHAR(100),
    @AcctNum NVARCHAR(20),
    @Description NVARCHAR(100),
    @Active BIT,
    @Classification NVARCHAR(50),
    @AccountType NVARCHAR(100),
    @AccountSubType NVARCHAR(100),
    @FullyQualifiedName NVARCHAR(500),
    @SubAccount BIT,
    @ParentRefValue NVARCHAR(50),
    @ParentRefName NVARCHAR(100),
    @CurrentBalance DECIMAL(18,2),
    @CurrentBalanceWithSubAccounts DECIMAL(18,2),
    @CurrencyRefValue NVARCHAR(10),
    @CurrencyRefName NVARCHAR(100)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Account]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = @SyncToken,
        [RealmId] = @RealmId,
        [Name] = @Name,
        [AcctNum] = @AcctNum,
        [Description] = @Description,
        [Active] = @Active,
        [Classification] = @Classification,
        [AccountType] = @AccountType,
        [AccountSubType] = @AccountSubType,
        [FullyQualifiedName] = @FullyQualifiedName,
        [SubAccount] = @SubAccount,
        [ParentRefValue] = @ParentRefValue,
        [ParentRefName] = @ParentRefName,
        [CurrentBalance] = @CurrentBalance,
        [CurrentBalanceWithSubAccounts] = @CurrentBalanceWithSubAccounts,
        [CurrencyRefValue] = @CurrencyRefValue,
        [CurrencyRefName] = @CurrencyRefName
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[Name],
        INSERTED.[AcctNum],
        INSERTED.[Description],
        INSERTED.[Active],
        INSERTED.[Classification],
        INSERTED.[AccountType],
        INSERTED.[AccountSubType],
        INSERTED.[FullyQualifiedName],
        INSERTED.[SubAccount],
        INSERTED.[ParentRefValue],
        INSERTED.[ParentRefName],
        INSERTED.[CurrentBalance],
        INSERTED.[CurrentBalanceWithSubAccounts],
        INSERTED.[CurrencyRefValue],
        INSERTED.[CurrencyRefName]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


DROP PROCEDURE IF EXISTS DeleteQboAccountByQboId;
GO

CREATE PROCEDURE DeleteQboAccountByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Account]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[Name],
        DELETED.[AcctNum],
        DELETED.[Description],
        DELETED.[Active],
        DELETED.[Classification],
        DELETED.[AccountType],
        DELETED.[AccountSubType],
        DELETED.[FullyQualifiedName],
        DELETED.[SubAccount],
        DELETED.[ParentRefValue],
        DELETED.[ParentRefName],
        DELETED.[CurrentBalance],
        DELETED.[CurrentBalanceWithSubAccounts],
        DELETED.[CurrencyRefValue],
        DELETED.[CurrencyRefName]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO
