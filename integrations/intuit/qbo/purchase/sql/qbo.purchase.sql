GO

GO

IF OBJECT_ID('qbo.Purchase', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Purchase]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [PaymentType] NVARCHAR(50) NULL,
    [AccountRefValue] NVARCHAR(50) NULL,
    [AccountRefName] NVARCHAR(500) NULL,
    [EntityRefValue] NVARCHAR(50) NULL,
    [EntityRefName] NVARCHAR(500) NULL,
    [Credit] BIT NULL,
    [TxnDate] NVARCHAR(50) NULL,
    [DocNumber] NVARCHAR(21) NULL,
    [PrivateNote] NVARCHAR(4000) NULL,
    [TotalAmt] DECIMAL(18,2) NULL,
    [CurrencyRefValue] NVARCHAR(10) NULL,
    [CurrencyRefName] NVARCHAR(100) NULL,
    [ExchangeRate] DECIMAL(18,6) NULL,
    [DepartmentRefValue] NVARCHAR(50) NULL,
    [DepartmentRefName] NVARCHAR(500) NULL,
    [GlobalTaxCalculation] NVARCHAR(50) NULL
);
END
GO

IF OBJECT_ID('qbo.Purchase', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboPurchase_QboId' AND object_id = OBJECT_ID('qbo.Purchase'))
BEGIN
CREATE INDEX IX_QboPurchase_QboId ON [qbo].[Purchase] ([QboId]);
END
GO

IF OBJECT_ID('qbo.Purchase', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboPurchase_RealmId' AND object_id = OBJECT_ID('qbo.Purchase'))
BEGIN
CREATE INDEX IX_QboPurchase_RealmId ON [qbo].[Purchase] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Purchase', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboPurchase_EntityRefValue' AND object_id = OBJECT_ID('qbo.Purchase'))
BEGIN
CREATE INDEX IX_QboPurchase_EntityRefValue ON [qbo].[Purchase] ([EntityRefValue]);
END
GO

IF OBJECT_ID('qbo.Purchase', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboPurchase_DocNumber' AND object_id = OBJECT_ID('qbo.Purchase'))
BEGIN
CREATE INDEX IX_QboPurchase_DocNumber ON [qbo].[Purchase] ([DocNumber]);
END
GO


IF OBJECT_ID('qbo.PurchaseLine', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[PurchaseLine]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboPurchaseId] BIGINT NOT NULL,
    [QboLineId] NVARCHAR(50) NULL,
    [LineNum] INT NULL,
    [Description] NVARCHAR(4000) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [DetailType] NVARCHAR(50) NULL,
    [ItemRefValue] NVARCHAR(50) NULL,
    [ItemRefName] NVARCHAR(500) NULL,
    [AccountRefValue] NVARCHAR(50) NULL,
    [AccountRefName] NVARCHAR(500) NULL,
    [CustomerRefValue] NVARCHAR(50) NULL,
    [CustomerRefName] NVARCHAR(500) NULL,
    [ClassRefValue] NVARCHAR(50) NULL,
    [ClassRefName] NVARCHAR(500) NULL,
    [BillableStatus] NVARCHAR(50) NULL,
    [Qty] DECIMAL(18,6) NULL,
    [UnitPrice] DECIMAL(18,6) NULL,
    [MarkupPercent] DECIMAL(18,6) NULL,
    CONSTRAINT [FK_QboPurchaseLine_QboPurchase] FOREIGN KEY ([QboPurchaseId]) REFERENCES [qbo].[Purchase]([Id]) ON DELETE CASCADE
);
END
GO

IF OBJECT_ID('qbo.PurchaseLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboPurchaseLine_QboPurchaseId' AND object_id = OBJECT_ID('qbo.PurchaseLine'))
BEGIN
CREATE INDEX IX_QboPurchaseLine_QboPurchaseId ON [qbo].[PurchaseLine] ([QboPurchaseId]);
END
GO

IF OBJECT_ID('qbo.PurchaseLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboPurchaseLine_QboLineId' AND object_id = OBJECT_ID('qbo.PurchaseLine'))
BEGIN
CREATE INDEX IX_QboPurchaseLine_QboLineId ON [qbo].[PurchaseLine] ([QboLineId]);
END
GO

IF OBJECT_ID('qbo.PurchaseLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboPurchaseLine_ItemRefValue' AND object_id = OBJECT_ID('qbo.PurchaseLine'))
BEGIN
CREATE INDEX IX_QboPurchaseLine_ItemRefValue ON [qbo].[PurchaseLine] ([ItemRefValue]);
END
GO


-- Purchase Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboPurchase
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @PaymentType NVARCHAR(50),
    @AccountRefValue NVARCHAR(50),
    @AccountRefName NVARCHAR(500),
    @EntityRefValue NVARCHAR(50),
    @EntityRefName NVARCHAR(500),
    @Credit BIT,
    @TxnDate NVARCHAR(50),
    @DocNumber NVARCHAR(21),
    @PrivateNote NVARCHAR(4000),
    @TotalAmt DECIMAL(18,2),
    @CurrencyRefValue NVARCHAR(10),
    @CurrencyRefName NVARCHAR(100),
    @ExchangeRate DECIMAL(18,6),
    @DepartmentRefValue NVARCHAR(50),
    @DepartmentRefName NVARCHAR(500),
    @GlobalTaxCalculation NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Purchase] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [PaymentType], [AccountRefValue], [AccountRefName], [EntityRefValue], [EntityRefName],
        [Credit], [TxnDate], [DocNumber], [PrivateNote], [TotalAmt],
        [CurrencyRefValue], [CurrencyRefName], [ExchangeRate], [DepartmentRefValue], [DepartmentRefName],
        [GlobalTaxCalculation]
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
        INSERTED.[PaymentType],
        INSERTED.[AccountRefValue],
        INSERTED.[AccountRefName],
        INSERTED.[EntityRefValue],
        INSERTED.[EntityRefName],
        INSERTED.[Credit],
        INSERTED.[TxnDate],
        INSERTED.[DocNumber],
        INSERTED.[PrivateNote],
        INSERTED.[TotalAmt],
        INSERTED.[CurrencyRefValue],
        INSERTED.[CurrencyRefName],
        INSERTED.[ExchangeRate],
        INSERTED.[DepartmentRefValue],
        INSERTED.[DepartmentRefName],
        INSERTED.[GlobalTaxCalculation]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @PaymentType, @AccountRefValue, @AccountRefName, @EntityRefValue, @EntityRefName,
        @Credit, @TxnDate, @DocNumber, @PrivateNote, @TotalAmt,
        @CurrencyRefValue, @CurrencyRefName, @ExchangeRate, @DepartmentRefValue, @DepartmentRefName,
        @GlobalTaxCalculation
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboPurchases
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
        [PaymentType],
        [AccountRefValue],
        [AccountRefName],
        [EntityRefValue],
        [EntityRefName],
        [Credit],
        [TxnDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Purchase]
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboPurchasesByRealmId
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
        [PaymentType],
        [AccountRefValue],
        [AccountRefName],
        [EntityRefValue],
        [EntityRefName],
        [Credit],
        [TxnDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Purchase]
    WHERE [RealmId] = @RealmId
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboPurchaseById
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
        [PaymentType],
        [AccountRefValue],
        [AccountRefName],
        [EntityRefValue],
        [EntityRefName],
        [Credit],
        [TxnDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Purchase]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboPurchaseByQboId
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
        [PaymentType],
        [AccountRefValue],
        [AccountRefName],
        [EntityRefValue],
        [EntityRefName],
        [Credit],
        [TxnDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Purchase]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboPurchaseByQboIdAndRealmId
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
        [PaymentType],
        [AccountRefValue],
        [AccountRefName],
        [EntityRefValue],
        [EntityRefName],
        [Credit],
        [TxnDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Purchase]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboPurchaseByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @PaymentType NVARCHAR(50),
    @AccountRefValue NVARCHAR(50),
    @AccountRefName NVARCHAR(500),
    @EntityRefValue NVARCHAR(50),
    @EntityRefName NVARCHAR(500),
    @Credit BIT,
    @TxnDate NVARCHAR(50),
    @DocNumber NVARCHAR(21),
    @PrivateNote NVARCHAR(4000),
    @TotalAmt DECIMAL(18,2),
    @CurrencyRefValue NVARCHAR(10),
    @CurrencyRefName NVARCHAR(100),
    @ExchangeRate DECIMAL(18,6),
    @DepartmentRefValue NVARCHAR(50),
    @DepartmentRefName NVARCHAR(500),
    @GlobalTaxCalculation NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Purchase]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = @SyncToken,
        [RealmId] = @RealmId,
        [PaymentType] = @PaymentType,
        [AccountRefValue] = @AccountRefValue,
        [AccountRefName] = @AccountRefName,
        [EntityRefValue] = @EntityRefValue,
        [EntityRefName] = @EntityRefName,
        [Credit] = @Credit,
        [TxnDate] = @TxnDate,
        [DocNumber] = @DocNumber,
        [PrivateNote] = @PrivateNote,
        [TotalAmt] = @TotalAmt,
        [CurrencyRefValue] = @CurrencyRefValue,
        [CurrencyRefName] = @CurrencyRefName,
        [ExchangeRate] = @ExchangeRate,
        [DepartmentRefValue] = @DepartmentRefValue,
        [DepartmentRefName] = @DepartmentRefName,
        [GlobalTaxCalculation] = @GlobalTaxCalculation
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboId],
        INSERTED.[SyncToken],
        INSERTED.[RealmId],
        INSERTED.[PaymentType],
        INSERTED.[AccountRefValue],
        INSERTED.[AccountRefName],
        INSERTED.[EntityRefValue],
        INSERTED.[EntityRefName],
        INSERTED.[Credit],
        INSERTED.[TxnDate],
        INSERTED.[DocNumber],
        INSERTED.[PrivateNote],
        INSERTED.[TotalAmt],
        INSERTED.[CurrencyRefValue],
        INSERTED.[CurrencyRefName],
        INSERTED.[ExchangeRate],
        INSERTED.[DepartmentRefValue],
        INSERTED.[DepartmentRefName],
        INSERTED.[GlobalTaxCalculation]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboPurchaseByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Purchase]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[PaymentType],
        DELETED.[AccountRefValue],
        DELETED.[AccountRefName],
        DELETED.[EntityRefValue],
        DELETED.[EntityRefName],
        DELETED.[Credit],
        DELETED.[TxnDate],
        DELETED.[DocNumber],
        DELETED.[PrivateNote],
        DELETED.[TotalAmt],
        DELETED.[CurrencyRefValue],
        DELETED.[CurrencyRefName],
        DELETED.[ExchangeRate],
        DELETED.[DepartmentRefValue],
        DELETED.[DepartmentRefName],
        DELETED.[GlobalTaxCalculation]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


-- PurchaseLine Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboPurchaseLine
(
    @QboPurchaseId BIGINT,
    @QboLineId NVARCHAR(50),
    @LineNum INT,
    @Description NVARCHAR(4000),
    @Amount DECIMAL(18,2),
    @DetailType NVARCHAR(50),
    @ItemRefValue NVARCHAR(50),
    @ItemRefName NVARCHAR(500),
    @AccountRefValue NVARCHAR(50),
    @AccountRefName NVARCHAR(500),
    @CustomerRefValue NVARCHAR(50),
    @CustomerRefName NVARCHAR(500),
    @ClassRefValue NVARCHAR(50),
    @ClassRefName NVARCHAR(500),
    @BillableStatus NVARCHAR(50),
    @Qty DECIMAL(18,6),
    @UnitPrice DECIMAL(18,6),
    @MarkupPercent DECIMAL(18,6)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[PurchaseLine] (
        [CreatedDatetime], [ModifiedDatetime], [QboPurchaseId], [QboLineId], [LineNum],
        [Description], [Amount], [DetailType], [ItemRefValue], [ItemRefName],
        [AccountRefValue], [AccountRefName], [CustomerRefValue], [CustomerRefName],
        [ClassRefValue], [ClassRefName], [BillableStatus], [Qty], [UnitPrice], [MarkupPercent]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboPurchaseId],
        INSERTED.[QboLineId],
        INSERTED.[LineNum],
        INSERTED.[Description],
        INSERTED.[Amount],
        INSERTED.[DetailType],
        INSERTED.[ItemRefValue],
        INSERTED.[ItemRefName],
        INSERTED.[AccountRefValue],
        INSERTED.[AccountRefName],
        INSERTED.[CustomerRefValue],
        INSERTED.[CustomerRefName],
        INSERTED.[ClassRefValue],
        INSERTED.[ClassRefName],
        INSERTED.[BillableStatus],
        INSERTED.[Qty],
        INSERTED.[UnitPrice],
        INSERTED.[MarkupPercent]
    VALUES (
        @Now, @Now, @QboPurchaseId, @QboLineId, @LineNum,
        @Description, @Amount, @DetailType, @ItemRefValue, @ItemRefName,
        @AccountRefValue, @AccountRefName, @CustomerRefValue, @CustomerRefName,
        @ClassRefValue, @ClassRefName, @BillableStatus, @Qty, @UnitPrice, @MarkupPercent
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboPurchaseLinesByQboPurchaseId
(
    @QboPurchaseId BIGINT
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
        [QboPurchaseId],
        [QboLineId],
        [LineNum],
        [Description],
        [Amount],
        [DetailType],
        [ItemRefValue],
        [ItemRefName],
        [AccountRefValue],
        [AccountRefName],
        [CustomerRefValue],
        [CustomerRefName],
        [ClassRefValue],
        [ClassRefName],
        [BillableStatus],
        [Qty],
        [UnitPrice],
        [MarkupPercent]
    FROM [qbo].[PurchaseLine]
    WHERE [QboPurchaseId] = @QboPurchaseId
    ORDER BY [LineNum] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboPurchaseLineByQboPurchaseIdAndQboLineId
(
    @QboPurchaseId BIGINT,
    @QboLineId NVARCHAR(50)
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
        [QboPurchaseId],
        [QboLineId],
        [LineNum],
        [Description],
        [Amount],
        [DetailType],
        [ItemRefValue],
        [ItemRefName],
        [AccountRefValue],
        [AccountRefName],
        [CustomerRefValue],
        [CustomerRefName],
        [ClassRefValue],
        [ClassRefName],
        [BillableStatus],
        [Qty],
        [UnitPrice],
        [MarkupPercent]
    FROM [qbo].[PurchaseLine]
    WHERE [QboPurchaseId] = @QboPurchaseId AND [QboLineId] = @QboLineId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboPurchaseLineById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @LineNum INT,
    @Description NVARCHAR(4000),
    @Amount DECIMAL(18,2),
    @DetailType NVARCHAR(50),
    @ItemRefValue NVARCHAR(50),
    @ItemRefName NVARCHAR(500),
    @AccountRefValue NVARCHAR(50),
    @AccountRefName NVARCHAR(500),
    @CustomerRefValue NVARCHAR(50),
    @CustomerRefName NVARCHAR(500),
    @ClassRefValue NVARCHAR(50),
    @ClassRefName NVARCHAR(500),
    @BillableStatus NVARCHAR(50),
    @Qty DECIMAL(18,6),
    @UnitPrice DECIMAL(18,6),
    @MarkupPercent DECIMAL(18,6)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[PurchaseLine]
    SET
        [ModifiedDatetime] = @Now,
        [LineNum] = @LineNum,
        [Description] = @Description,
        [Amount] = @Amount,
        [DetailType] = @DetailType,
        [ItemRefValue] = @ItemRefValue,
        [ItemRefName] = @ItemRefName,
        [AccountRefValue] = @AccountRefValue,
        [AccountRefName] = @AccountRefName,
        [CustomerRefValue] = @CustomerRefValue,
        [CustomerRefName] = @CustomerRefName,
        [ClassRefValue] = @ClassRefValue,
        [ClassRefName] = @ClassRefName,
        [BillableStatus] = @BillableStatus,
        [Qty] = @Qty,
        [UnitPrice] = @UnitPrice,
        [MarkupPercent] = @MarkupPercent
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboPurchaseId],
        INSERTED.[QboLineId],
        INSERTED.[LineNum],
        INSERTED.[Description],
        INSERTED.[Amount],
        INSERTED.[DetailType],
        INSERTED.[ItemRefValue],
        INSERTED.[ItemRefName],
        INSERTED.[AccountRefValue],
        INSERTED.[AccountRefName],
        INSERTED.[CustomerRefValue],
        INSERTED.[CustomerRefName],
        INSERTED.[ClassRefValue],
        INSERTED.[ClassRefName],
        INSERTED.[BillableStatus],
        INSERTED.[Qty],
        INSERTED.[UnitPrice],
        INSERTED.[MarkupPercent]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboPurchaseLineById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[PurchaseLine]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboPurchaseId],
        DELETED.[QboLineId],
        DELETED.[LineNum],
        DELETED.[Description],
        DELETED.[Amount],
        DELETED.[DetailType],
        DELETED.[ItemRefValue],
        DELETED.[ItemRefName],
        DELETED.[AccountRefValue],
        DELETED.[AccountRefName],
        DELETED.[CustomerRefValue],
        DELETED.[CustomerRefName],
        DELETED.[ClassRefValue],
        DELETED.[ClassRefName],
        DELETED.[BillableStatus],
        DELETED.[Qty],
        DELETED.[UnitPrice],
        DELETED.[MarkupPercent]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
