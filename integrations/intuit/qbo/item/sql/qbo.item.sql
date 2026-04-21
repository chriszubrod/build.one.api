GO

IF OBJECT_ID('qbo.Item', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Item]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [Name] NVARCHAR(255) NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Active] BIT NULL,
    [Type] NVARCHAR(50) NULL,
    [ParentRefValue] NVARCHAR(50) NULL,
    [ParentRefName] NVARCHAR(255) NULL,
    [Level] INT NULL,
    [FullyQualifiedName] NVARCHAR(MAX) NULL,
    [Sku] NVARCHAR(100) NULL,
    [UnitPrice] DECIMAL(18,2) NULL,
    [PurchaseCost] DECIMAL(18,2) NULL,
    [Taxable] BIT NULL,
    [IncomeAccountRefValue] NVARCHAR(50) NULL,
    [IncomeAccountRefName] NVARCHAR(255) NULL,
    [ExpenseAccountRefValue] NVARCHAR(50) NULL,
    [ExpenseAccountRefName] NVARCHAR(255) NULL
);
END
GO

IF OBJECT_ID('qbo.Item', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboItem_QboId' AND object_id = OBJECT_ID('qbo.Item'))
BEGIN
CREATE INDEX IX_QboItem_QboId ON [qbo].[Item] ([QboId]);
END
GO

IF OBJECT_ID('qbo.Item', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboItem_RealmId' AND object_id = OBJECT_ID('qbo.Item'))
BEGIN
CREATE INDEX IX_QboItem_RealmId ON [qbo].[Item] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Item', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboItem_ParentRefValue' AND object_id = OBJECT_ID('qbo.Item'))
BEGIN
CREATE INDEX IX_QboItem_ParentRefValue ON [qbo].[Item] ([ParentRefValue]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateQboItem
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(MAX),
    @Active BIT,
    @Type NVARCHAR(50),
    @ParentRefValue NVARCHAR(50),
    @ParentRefName NVARCHAR(255),
    @Level INT,
    @FullyQualifiedName NVARCHAR(MAX),
    @Sku NVARCHAR(100),
    @UnitPrice DECIMAL(18,2),
    @PurchaseCost DECIMAL(18,2),
    @Taxable BIT,
    @IncomeAccountRefValue NVARCHAR(50),
    @IncomeAccountRefName NVARCHAR(255),
    @ExpenseAccountRefValue NVARCHAR(50),
    @ExpenseAccountRefName NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Item] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [Name], [Description], [Active], [Type], [ParentRefValue], [ParentRefName],
        [Level], [FullyQualifiedName], [Sku], [UnitPrice], [PurchaseCost], [Taxable],
        [IncomeAccountRefValue], [IncomeAccountRefName], [ExpenseAccountRefValue], [ExpenseAccountRefName]
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
        INSERTED.[Description],
        INSERTED.[Active],
        INSERTED.[Type],
        INSERTED.[ParentRefValue],
        INSERTED.[ParentRefName],
        INSERTED.[Level],
        INSERTED.[FullyQualifiedName],
        INSERTED.[Sku],
        INSERTED.[UnitPrice],
        INSERTED.[PurchaseCost],
        INSERTED.[Taxable],
        INSERTED.[IncomeAccountRefValue],
        INSERTED.[IncomeAccountRefName],
        INSERTED.[ExpenseAccountRefValue],
        INSERTED.[ExpenseAccountRefName]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @Name, @Description, @Active, @Type, @ParentRefValue, @ParentRefName,
        @Level, @FullyQualifiedName, @Sku, @UnitPrice, @PurchaseCost, @Taxable,
        @IncomeAccountRefValue, @IncomeAccountRefName, @ExpenseAccountRefValue, @ExpenseAccountRefName
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboItems
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
        [Description],
        [Active],
        [Type],
        [ParentRefValue],
        [ParentRefName],
        [Level],
        [FullyQualifiedName],
        [Sku],
        [UnitPrice],
        [PurchaseCost],
        [Taxable],
        [IncomeAccountRefValue],
        [IncomeAccountRefName],
        [ExpenseAccountRefValue],
        [ExpenseAccountRefName]
    FROM [qbo].[Item]
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO



GO

CREATE OR ALTER PROCEDURE ReadQboItemsByRealmId
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
        [Description],
        [Active],
        [Type],
        [ParentRefValue],
        [ParentRefName],
        [Level],
        [FullyQualifiedName],
        [Sku],
        [UnitPrice],
        [PurchaseCost],
        [Taxable],
        [IncomeAccountRefValue],
        [IncomeAccountRefName],
        [ExpenseAccountRefValue],
        [ExpenseAccountRefName]
    FROM [qbo].[Item]
    WHERE [RealmId] = @RealmId
    ORDER BY [Name] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboItemById
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
        [Description],
        [Active],
        [Type],
        [ParentRefValue],
        [ParentRefName],
        [Level],
        [FullyQualifiedName],
        [Sku],
        [UnitPrice],
        [PurchaseCost],
        [Taxable],
        [IncomeAccountRefValue],
        [IncomeAccountRefName],
        [ExpenseAccountRefValue],
        [ExpenseAccountRefName]
    FROM [qbo].[Item]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboItemByQboId
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
        [Description],
        [Active],
        [Type],
        [ParentRefValue],
        [ParentRefName],
        [Level],
        [FullyQualifiedName],
        [Sku],
        [UnitPrice],
        [PurchaseCost],
        [Taxable],
        [IncomeAccountRefValue],
        [IncomeAccountRefName],
        [ExpenseAccountRefValue],
        [ExpenseAccountRefName]
    FROM [qbo].[Item]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboItemByQboIdAndRealmId
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
        [Description],
        [Active],
        [Type],
        [ParentRefValue],
        [ParentRefName],
        [Level],
        [FullyQualifiedName],
        [Sku],
        [UnitPrice],
        [PurchaseCost],
        [Taxable],
        [IncomeAccountRefValue],
        [IncomeAccountRefName],
        [ExpenseAccountRefValue],
        [ExpenseAccountRefName]
    FROM [qbo].[Item]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboItemByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @Name NVARCHAR(255),
    @Description NVARCHAR(MAX),
    @Active BIT,
    @Type NVARCHAR(50),
    @ParentRefValue NVARCHAR(50),
    @ParentRefName NVARCHAR(255),
    @Level INT,
    @FullyQualifiedName NVARCHAR(MAX),
    @Sku NVARCHAR(100),
    @UnitPrice DECIMAL(18,2),
    @PurchaseCost DECIMAL(18,2),
    @Taxable BIT,
    @IncomeAccountRefValue NVARCHAR(50),
    @IncomeAccountRefName NVARCHAR(255),
    @ExpenseAccountRefValue NVARCHAR(50),
    @ExpenseAccountRefName NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Item]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = CASE WHEN @SyncToken IS NULL THEN [SyncToken] ELSE @SyncToken END,
        [RealmId] = CASE WHEN @RealmId IS NULL THEN [RealmId] ELSE @RealmId END,
        [Name] = CASE WHEN @Name IS NULL THEN [Name] ELSE @Name END,
        [Description] = CASE WHEN @Description IS NULL THEN [Description] ELSE @Description END,
        [Active] = CASE WHEN @Active IS NULL THEN [Active] ELSE @Active END,
        [Type] = CASE WHEN @Type IS NULL THEN [Type] ELSE @Type END,
        [ParentRefValue] = CASE WHEN @ParentRefValue IS NULL THEN [ParentRefValue] ELSE @ParentRefValue END,
        [ParentRefName] = CASE WHEN @ParentRefName IS NULL THEN [ParentRefName] ELSE @ParentRefName END,
        [Level] = CASE WHEN @Level IS NULL THEN [Level] ELSE @Level END,
        [FullyQualifiedName] = CASE WHEN @FullyQualifiedName IS NULL THEN [FullyQualifiedName] ELSE @FullyQualifiedName END,
        [Sku] = CASE WHEN @Sku IS NULL THEN [Sku] ELSE @Sku END,
        [UnitPrice] = CASE WHEN @UnitPrice IS NULL THEN [UnitPrice] ELSE @UnitPrice END,
        [PurchaseCost] = CASE WHEN @PurchaseCost IS NULL THEN [PurchaseCost] ELSE @PurchaseCost END,
        [Taxable] = CASE WHEN @Taxable IS NULL THEN [Taxable] ELSE @Taxable END,
        [IncomeAccountRefValue] = CASE WHEN @IncomeAccountRefValue IS NULL THEN [IncomeAccountRefValue] ELSE @IncomeAccountRefValue END,
        [IncomeAccountRefName] = CASE WHEN @IncomeAccountRefName IS NULL THEN [IncomeAccountRefName] ELSE @IncomeAccountRefName END,
        [ExpenseAccountRefValue] = CASE WHEN @ExpenseAccountRefValue IS NULL THEN [ExpenseAccountRefValue] ELSE @ExpenseAccountRefValue END,
        [ExpenseAccountRefName] = CASE WHEN @ExpenseAccountRefName IS NULL THEN [ExpenseAccountRefName] ELSE @ExpenseAccountRefName END
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
        INSERTED.[Description],
        INSERTED.[Active],
        INSERTED.[Type],
        INSERTED.[ParentRefValue],
        INSERTED.[ParentRefName],
        INSERTED.[Level],
        INSERTED.[FullyQualifiedName],
        INSERTED.[Sku],
        INSERTED.[UnitPrice],
        INSERTED.[PurchaseCost],
        INSERTED.[Taxable],
        INSERTED.[IncomeAccountRefValue],
        INSERTED.[IncomeAccountRefName],
        INSERTED.[ExpenseAccountRefValue],
        INSERTED.[ExpenseAccountRefName]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboItemByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Item]
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
        DELETED.[Description],
        DELETED.[Active],
        DELETED.[Type],
        DELETED.[ParentRefValue],
        DELETED.[ParentRefName],
        DELETED.[Level],
        DELETED.[FullyQualifiedName],
        DELETED.[Sku],
        DELETED.[UnitPrice],
        DELETED.[PurchaseCost],
        DELETED.[Taxable],
        DELETED.[IncomeAccountRefValue],
        DELETED.[IncomeAccountRefName],
        DELETED.[ExpenseAccountRefValue],
        DELETED.[ExpenseAccountRefName]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO

