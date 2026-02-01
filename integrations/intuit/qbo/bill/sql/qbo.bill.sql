GO

GO

IF OBJECT_ID('qbo.Bill', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Bill]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [VendorRefValue] NVARCHAR(50) NULL,
    [VendorRefName] NVARCHAR(500) NULL,
    [TxnDate] NVARCHAR(50) NULL,
    [DueDate] NVARCHAR(50) NULL,
    [DocNumber] NVARCHAR(21) NULL,
    [PrivateNote] NVARCHAR(4000) NULL,
    [TotalAmt] DECIMAL(18,2) NULL,
    [Balance] DECIMAL(18,2) NULL,
    [ApAccountRefValue] NVARCHAR(50) NULL,
    [ApAccountRefName] NVARCHAR(500) NULL,
    [SalesTermRefValue] NVARCHAR(50) NULL,
    [SalesTermRefName] NVARCHAR(500) NULL,
    [CurrencyRefValue] NVARCHAR(10) NULL,
    [CurrencyRefName] NVARCHAR(100) NULL,
    [ExchangeRate] DECIMAL(18,6) NULL,
    [DepartmentRefValue] NVARCHAR(50) NULL,
    [DepartmentRefName] NVARCHAR(500) NULL,
    [GlobalTaxCalculation] NVARCHAR(50) NULL
);
END
GO

IF OBJECT_ID('qbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboBill_QboId' AND object_id = OBJECT_ID('qbo.Bill'))
BEGIN
CREATE INDEX IX_QboBill_QboId ON [qbo].[Bill] ([QboId]);
END
GO

IF OBJECT_ID('qbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboBill_RealmId' AND object_id = OBJECT_ID('qbo.Bill'))
BEGIN
CREATE INDEX IX_QboBill_RealmId ON [qbo].[Bill] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboBill_VendorRefValue' AND object_id = OBJECT_ID('qbo.Bill'))
BEGIN
CREATE INDEX IX_QboBill_VendorRefValue ON [qbo].[Bill] ([VendorRefValue]);
END
GO

IF OBJECT_ID('qbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboBill_DocNumber' AND object_id = OBJECT_ID('qbo.Bill'))
BEGIN
CREATE INDEX IX_QboBill_DocNumber ON [qbo].[Bill] ([DocNumber]);
END
GO


IF OBJECT_ID('qbo.BillLine', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[BillLine]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboBillId] BIGINT NOT NULL,
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
    CONSTRAINT [FK_QboBillLine_QboBill] FOREIGN KEY ([QboBillId]) REFERENCES [qbo].[Bill]([Id]) ON DELETE CASCADE
);
END
GO

IF OBJECT_ID('qbo.BillLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboBillLine_QboBillId' AND object_id = OBJECT_ID('qbo.BillLine'))
BEGIN
CREATE INDEX IX_QboBillLine_QboBillId ON [qbo].[BillLine] ([QboBillId]);
END
GO

IF OBJECT_ID('qbo.BillLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboBillLine_QboLineId' AND object_id = OBJECT_ID('qbo.BillLine'))
BEGIN
CREATE INDEX IX_QboBillLine_QboLineId ON [qbo].[BillLine] ([QboLineId]);
END
GO

IF OBJECT_ID('qbo.BillLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboBillLine_ItemRefValue' AND object_id = OBJECT_ID('qbo.BillLine'))
BEGIN
CREATE INDEX IX_QboBillLine_ItemRefValue ON [qbo].[BillLine] ([ItemRefValue]);
END
GO


-- Bill Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboBill
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @VendorRefValue NVARCHAR(50),
    @VendorRefName NVARCHAR(500),
    @TxnDate NVARCHAR(50),
    @DueDate NVARCHAR(50),
    @DocNumber NVARCHAR(21),
    @PrivateNote NVARCHAR(4000),
    @TotalAmt DECIMAL(18,2),
    @Balance DECIMAL(18,2),
    @ApAccountRefValue NVARCHAR(50),
    @ApAccountRefName NVARCHAR(500),
    @SalesTermRefValue NVARCHAR(50),
    @SalesTermRefName NVARCHAR(500),
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

    INSERT INTO [qbo].[Bill] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [VendorRefValue], [VendorRefName], [TxnDate], [DueDate], [DocNumber],
        [PrivateNote], [TotalAmt], [Balance], [ApAccountRefValue], [ApAccountRefName],
        [SalesTermRefValue], [SalesTermRefName], [CurrencyRefValue], [CurrencyRefName],
        [ExchangeRate], [DepartmentRefValue], [DepartmentRefName], [GlobalTaxCalculation]
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
        INSERTED.[VendorRefValue],
        INSERTED.[VendorRefName],
        INSERTED.[TxnDate],
        INSERTED.[DueDate],
        INSERTED.[DocNumber],
        INSERTED.[PrivateNote],
        INSERTED.[TotalAmt],
        INSERTED.[Balance],
        INSERTED.[ApAccountRefValue],
        INSERTED.[ApAccountRefName],
        INSERTED.[SalesTermRefValue],
        INSERTED.[SalesTermRefName],
        INSERTED.[CurrencyRefValue],
        INSERTED.[CurrencyRefName],
        INSERTED.[ExchangeRate],
        INSERTED.[DepartmentRefValue],
        INSERTED.[DepartmentRefName],
        INSERTED.[GlobalTaxCalculation]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @VendorRefValue, @VendorRefName, @TxnDate, @DueDate, @DocNumber,
        @PrivateNote, @TotalAmt, @Balance, @ApAccountRefValue, @ApAccountRefName,
        @SalesTermRefValue, @SalesTermRefName, @CurrencyRefValue, @CurrencyRefName,
        @ExchangeRate, @DepartmentRefValue, @DepartmentRefName, @GlobalTaxCalculation
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboBills
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
        [VendorRefValue],
        [VendorRefName],
        [TxnDate],
        [DueDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [Balance],
        [ApAccountRefValue],
        [ApAccountRefName],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Bill]
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboBillsByRealmId
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
        [VendorRefValue],
        [VendorRefName],
        [TxnDate],
        [DueDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [Balance],
        [ApAccountRefValue],
        [ApAccountRefName],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Bill]
    WHERE [RealmId] = @RealmId
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboBillById
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
        [VendorRefValue],
        [VendorRefName],
        [TxnDate],
        [DueDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [Balance],
        [ApAccountRefValue],
        [ApAccountRefName],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Bill]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboBillByQboId
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
        [VendorRefValue],
        [VendorRefName],
        [TxnDate],
        [DueDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [Balance],
        [ApAccountRefValue],
        [ApAccountRefName],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Bill]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboBillByQboIdAndRealmId
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
        [VendorRefValue],
        [VendorRefName],
        [TxnDate],
        [DueDate],
        [DocNumber],
        [PrivateNote],
        [TotalAmt],
        [Balance],
        [ApAccountRefValue],
        [ApAccountRefName],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [GlobalTaxCalculation]
    FROM [qbo].[Bill]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboBillByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @VendorRefValue NVARCHAR(50),
    @VendorRefName NVARCHAR(500),
    @TxnDate NVARCHAR(50),
    @DueDate NVARCHAR(50),
    @DocNumber NVARCHAR(21),
    @PrivateNote NVARCHAR(4000),
    @TotalAmt DECIMAL(18,2),
    @Balance DECIMAL(18,2),
    @ApAccountRefValue NVARCHAR(50),
    @ApAccountRefName NVARCHAR(500),
    @SalesTermRefValue NVARCHAR(50),
    @SalesTermRefName NVARCHAR(500),
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

    UPDATE [qbo].[Bill]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = @SyncToken,
        [RealmId] = @RealmId,
        [VendorRefValue] = @VendorRefValue,
        [VendorRefName] = @VendorRefName,
        [TxnDate] = @TxnDate,
        [DueDate] = @DueDate,
        [DocNumber] = @DocNumber,
        [PrivateNote] = @PrivateNote,
        [TotalAmt] = @TotalAmt,
        [Balance] = @Balance,
        [ApAccountRefValue] = @ApAccountRefValue,
        [ApAccountRefName] = @ApAccountRefName,
        [SalesTermRefValue] = @SalesTermRefValue,
        [SalesTermRefName] = @SalesTermRefName,
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
        INSERTED.[VendorRefValue],
        INSERTED.[VendorRefName],
        INSERTED.[TxnDate],
        INSERTED.[DueDate],
        INSERTED.[DocNumber],
        INSERTED.[PrivateNote],
        INSERTED.[TotalAmt],
        INSERTED.[Balance],
        INSERTED.[ApAccountRefValue],
        INSERTED.[ApAccountRefName],
        INSERTED.[SalesTermRefValue],
        INSERTED.[SalesTermRefName],
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

CREATE OR ALTER PROCEDURE DeleteQboBillByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Bill]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[VendorRefValue],
        DELETED.[VendorRefName],
        DELETED.[TxnDate],
        DELETED.[DueDate],
        DELETED.[DocNumber],
        DELETED.[PrivateNote],
        DELETED.[TotalAmt],
        DELETED.[Balance],
        DELETED.[ApAccountRefValue],
        DELETED.[ApAccountRefName],
        DELETED.[SalesTermRefValue],
        DELETED.[SalesTermRefName],
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


-- BillLine Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboBillLine
(
    @QboBillId BIGINT,
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

    INSERT INTO [qbo].[BillLine] (
        [CreatedDatetime], [ModifiedDatetime], [QboBillId], [QboLineId], [LineNum],
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
        INSERTED.[QboBillId],
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
        @Now, @Now, @QboBillId, @QboLineId, @LineNum,
        @Description, @Amount, @DetailType, @ItemRefValue, @ItemRefName,
        @AccountRefValue, @AccountRefName, @CustomerRefValue, @CustomerRefName,
        @ClassRefValue, @ClassRefName, @BillableStatus, @Qty, @UnitPrice, @MarkupPercent
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboBillLinesByQboBillId
(
    @QboBillId BIGINT
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
        [QboBillId],
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
    FROM [qbo].[BillLine]
    WHERE [QboBillId] = @QboBillId
    ORDER BY [LineNum] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboBillLineByQboBillIdAndQboLineId
(
    @QboBillId BIGINT,
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
        [QboBillId],
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
    FROM [qbo].[BillLine]
    WHERE [QboBillId] = @QboBillId AND [QboLineId] = @QboLineId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboBillLineById
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

    UPDATE [qbo].[BillLine]
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
        INSERTED.[QboBillId],
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

CREATE OR ALTER PROCEDURE DeleteQboBillLineById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[BillLine]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboBillId],
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





SELECT BillableStatus, COUNT(*) as cnt 
FROM qbo.BillLine 
GROUP BY BillableStatus
ORDER BY cnt DESC;

-- Update IsBillable in dbo.BillLineItem based on qbo.BillLine.BillableStatus
-- "Billable" or "HasBeenBilled" = 1 (True), "NotBillable" = 0 (False)

UPDATE bli
SET bli.[IsBillable] = CASE 
    WHEN bl.[BillableStatus] IN ('Billable', 'HasBeenBilled') THEN 1
    WHEN bl.[BillableStatus] = 'NotBillable' THEN 0
    ELSE bli.[IsBillable]  -- Keep existing if NULL
END
FROM dbo.[BillLineItem] bli
INNER JOIN qbo.[BillLineItemBillLine] map ON map.[BillLineItemId] = bli.[Id]
INNER JOIN qbo.[BillLine] bl ON bl.[Id] = map.[QboBillLineId]
WHERE bl.[BillableStatus] IS NOT NULL;
