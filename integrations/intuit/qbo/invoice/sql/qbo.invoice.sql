GO

GO

IF OBJECT_ID('qbo.Invoice', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[Invoice]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboId] NVARCHAR(50) NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [CustomerRefValue] NVARCHAR(50) NULL,
    [CustomerRefName] NVARCHAR(500) NULL,
    [TxnDate] NVARCHAR(50) NULL,
    [DueDate] NVARCHAR(50) NULL,
    [ShipDate] NVARCHAR(50) NULL,
    [DocNumber] NVARCHAR(21) NULL,
    [PrivateNote] NVARCHAR(4000) NULL,
    [CustomerMemo] NVARCHAR(4000) NULL,
    [BillEmail] NVARCHAR(500) NULL,
    [TotalAmt] DECIMAL(18,2) NULL,
    [Balance] DECIMAL(18,2) NULL,
    [Deposit] DECIMAL(18,2) NULL,
    [SalesTermRefValue] NVARCHAR(50) NULL,
    [SalesTermRefName] NVARCHAR(500) NULL,
    [CurrencyRefValue] NVARCHAR(10) NULL,
    [CurrencyRefName] NVARCHAR(100) NULL,
    [ExchangeRate] DECIMAL(18,6) NULL,
    [DepartmentRefValue] NVARCHAR(50) NULL,
    [DepartmentRefName] NVARCHAR(500) NULL,
    [ClassRefValue] NVARCHAR(50) NULL,
    [ClassRefName] NVARCHAR(500) NULL,
    [ShipMethodRefValue] NVARCHAR(50) NULL,
    [ShipMethodRefName] NVARCHAR(500) NULL,
    [TrackingNum] NVARCHAR(500) NULL,
    [PrintStatus] NVARCHAR(50) NULL,
    [EmailStatus] NVARCHAR(50) NULL,
    [AllowOnlineACHPayment] BIT NULL,
    [AllowOnlineCreditCardPayment] BIT NULL,
    [ApplyTaxAfterDiscount] BIT NULL,
    [GlobalTaxCalculation] NVARCHAR(50) NULL
);
END
GO

IF OBJECT_ID('qbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoice_QboId' AND object_id = OBJECT_ID('qbo.Invoice'))
BEGIN
CREATE INDEX IX_QboInvoice_QboId ON [qbo].[Invoice] ([QboId]);
END
GO

IF OBJECT_ID('qbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoice_RealmId' AND object_id = OBJECT_ID('qbo.Invoice'))
BEGIN
CREATE INDEX IX_QboInvoice_RealmId ON [qbo].[Invoice] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoice_CustomerRefValue' AND object_id = OBJECT_ID('qbo.Invoice'))
BEGIN
CREATE INDEX IX_QboInvoice_CustomerRefValue ON [qbo].[Invoice] ([CustomerRefValue]);
END
GO

IF OBJECT_ID('qbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoice_DocNumber' AND object_id = OBJECT_ID('qbo.Invoice'))
BEGIN
CREATE INDEX IX_QboInvoice_DocNumber ON [qbo].[Invoice] ([DocNumber]);
END
GO


IF OBJECT_ID('qbo.InvoiceLine', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[InvoiceLine]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboInvoiceId] BIGINT NOT NULL,
    [QboLineId] NVARCHAR(50) NULL,
    [LineNum] INT NULL,
    [Description] NVARCHAR(4000) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [DetailType] NVARCHAR(50) NULL,
    [ItemRefValue] NVARCHAR(50) NULL,
    [ItemRefName] NVARCHAR(500) NULL,
    [ClassRefValue] NVARCHAR(50) NULL,
    [ClassRefName] NVARCHAR(500) NULL,
    [Qty] DECIMAL(18,6) NULL,
    [UnitPrice] DECIMAL(18,6) NULL,
    [TaxCodeRefValue] NVARCHAR(50) NULL,
    [TaxCodeRefName] NVARCHAR(500) NULL,
    [ServiceDate] NVARCHAR(50) NULL,
    [DiscountRate] DECIMAL(18,6) NULL,
    [DiscountAmt] DECIMAL(18,6) NULL,
    CONSTRAINT [FK_QboInvoiceLine_QboInvoice] FOREIGN KEY ([QboInvoiceId]) REFERENCES [qbo].[Invoice]([Id]) ON DELETE CASCADE
);
END
GO

IF OBJECT_ID('qbo.InvoiceLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoiceLine_QboInvoiceId' AND object_id = OBJECT_ID('qbo.InvoiceLine'))
BEGIN
CREATE INDEX IX_QboInvoiceLine_QboInvoiceId ON [qbo].[InvoiceLine] ([QboInvoiceId]);
END
GO

IF OBJECT_ID('qbo.InvoiceLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoiceLine_QboLineId' AND object_id = OBJECT_ID('qbo.InvoiceLine'))
BEGIN
CREATE INDEX IX_QboInvoiceLine_QboLineId ON [qbo].[InvoiceLine] ([QboLineId]);
END
GO

IF OBJECT_ID('qbo.InvoiceLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoiceLine_ItemRefValue' AND object_id = OBJECT_ID('qbo.InvoiceLine'))
BEGIN
CREATE INDEX IX_QboInvoiceLine_ItemRefValue ON [qbo].[InvoiceLine] ([ItemRefValue]);
END
GO


-- Invoice Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboInvoice
(
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @CustomerRefValue NVARCHAR(50),
    @CustomerRefName NVARCHAR(500),
    @TxnDate NVARCHAR(50),
    @DueDate NVARCHAR(50),
    @ShipDate NVARCHAR(50),
    @DocNumber NVARCHAR(21),
    @PrivateNote NVARCHAR(4000),
    @CustomerMemo NVARCHAR(4000),
    @BillEmail NVARCHAR(500),
    @TotalAmt DECIMAL(18,2),
    @Balance DECIMAL(18,2),
    @Deposit DECIMAL(18,2),
    @SalesTermRefValue NVARCHAR(50),
    @SalesTermRefName NVARCHAR(500),
    @CurrencyRefValue NVARCHAR(10),
    @CurrencyRefName NVARCHAR(100),
    @ExchangeRate DECIMAL(18,6),
    @DepartmentRefValue NVARCHAR(50),
    @DepartmentRefName NVARCHAR(500),
    @ClassRefValue NVARCHAR(50),
    @ClassRefName NVARCHAR(500),
    @ShipMethodRefValue NVARCHAR(50),
    @ShipMethodRefName NVARCHAR(500),
    @TrackingNum NVARCHAR(500),
    @PrintStatus NVARCHAR(50),
    @EmailStatus NVARCHAR(50),
    @AllowOnlineACHPayment BIT,
    @AllowOnlineCreditCardPayment BIT,
    @ApplyTaxAfterDiscount BIT,
    @GlobalTaxCalculation NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[Invoice] (
        [CreatedDatetime], [ModifiedDatetime], [QboId], [SyncToken], [RealmId],
        [CustomerRefValue], [CustomerRefName], [TxnDate], [DueDate], [ShipDate],
        [DocNumber], [PrivateNote], [CustomerMemo], [BillEmail],
        [TotalAmt], [Balance], [Deposit],
        [SalesTermRefValue], [SalesTermRefName], [CurrencyRefValue], [CurrencyRefName],
        [ExchangeRate], [DepartmentRefValue], [DepartmentRefName],
        [ClassRefValue], [ClassRefName], [ShipMethodRefValue], [ShipMethodRefName],
        [TrackingNum], [PrintStatus], [EmailStatus],
        [AllowOnlineACHPayment], [AllowOnlineCreditCardPayment],
        [ApplyTaxAfterDiscount], [GlobalTaxCalculation]
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
        INSERTED.[CustomerRefValue],
        INSERTED.[CustomerRefName],
        INSERTED.[TxnDate],
        INSERTED.[DueDate],
        INSERTED.[ShipDate],
        INSERTED.[DocNumber],
        INSERTED.[PrivateNote],
        INSERTED.[CustomerMemo],
        INSERTED.[BillEmail],
        INSERTED.[TotalAmt],
        INSERTED.[Balance],
        INSERTED.[Deposit],
        INSERTED.[SalesTermRefValue],
        INSERTED.[SalesTermRefName],
        INSERTED.[CurrencyRefValue],
        INSERTED.[CurrencyRefName],
        INSERTED.[ExchangeRate],
        INSERTED.[DepartmentRefValue],
        INSERTED.[DepartmentRefName],
        INSERTED.[ClassRefValue],
        INSERTED.[ClassRefName],
        INSERTED.[ShipMethodRefValue],
        INSERTED.[ShipMethodRefName],
        INSERTED.[TrackingNum],
        INSERTED.[PrintStatus],
        INSERTED.[EmailStatus],
        INSERTED.[AllowOnlineACHPayment],
        INSERTED.[AllowOnlineCreditCardPayment],
        INSERTED.[ApplyTaxAfterDiscount],
        INSERTED.[GlobalTaxCalculation]
    VALUES (
        @Now, @Now, @QboId, @SyncToken, @RealmId,
        @CustomerRefValue, @CustomerRefName, @TxnDate, @DueDate, @ShipDate,
        @DocNumber, @PrivateNote, @CustomerMemo, @BillEmail,
        @TotalAmt, @Balance, @Deposit,
        @SalesTermRefValue, @SalesTermRefName, @CurrencyRefValue, @CurrencyRefName,
        @ExchangeRate, @DepartmentRefValue, @DepartmentRefName,
        @ClassRefValue, @ClassRefName, @ShipMethodRefValue, @ShipMethodRefName,
        @TrackingNum, @PrintStatus, @EmailStatus,
        @AllowOnlineACHPayment, @AllowOnlineCreditCardPayment,
        @ApplyTaxAfterDiscount, @GlobalTaxCalculation
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboInvoices
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
        [CustomerRefValue],
        [CustomerRefName],
        [TxnDate],
        [DueDate],
        [ShipDate],
        [DocNumber],
        [PrivateNote],
        [CustomerMemo],
        [BillEmail],
        [TotalAmt],
        [Balance],
        [Deposit],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [ClassRefValue],
        [ClassRefName],
        [ShipMethodRefValue],
        [ShipMethodRefName],
        [TrackingNum],
        [PrintStatus],
        [EmailStatus],
        [AllowOnlineACHPayment],
        [AllowOnlineCreditCardPayment],
        [ApplyTaxAfterDiscount],
        [GlobalTaxCalculation]
    FROM [qbo].[Invoice]
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboInvoicesByRealmId
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
        [CustomerRefValue],
        [CustomerRefName],
        [TxnDate],
        [DueDate],
        [ShipDate],
        [DocNumber],
        [PrivateNote],
        [CustomerMemo],
        [BillEmail],
        [TotalAmt],
        [Balance],
        [Deposit],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [ClassRefValue],
        [ClassRefName],
        [ShipMethodRefValue],
        [ShipMethodRefName],
        [TrackingNum],
        [PrintStatus],
        [EmailStatus],
        [AllowOnlineACHPayment],
        [AllowOnlineCreditCardPayment],
        [ApplyTaxAfterDiscount],
        [GlobalTaxCalculation]
    FROM [qbo].[Invoice]
    WHERE [RealmId] = @RealmId
    ORDER BY [TxnDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboInvoiceById
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
        [CustomerRefValue],
        [CustomerRefName],
        [TxnDate],
        [DueDate],
        [ShipDate],
        [DocNumber],
        [PrivateNote],
        [CustomerMemo],
        [BillEmail],
        [TotalAmt],
        [Balance],
        [Deposit],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [ClassRefValue],
        [ClassRefName],
        [ShipMethodRefValue],
        [ShipMethodRefName],
        [TrackingNum],
        [PrintStatus],
        [EmailStatus],
        [AllowOnlineACHPayment],
        [AllowOnlineCreditCardPayment],
        [ApplyTaxAfterDiscount],
        [GlobalTaxCalculation]
    FROM [qbo].[Invoice]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboInvoiceByQboId
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
        [CustomerRefValue],
        [CustomerRefName],
        [TxnDate],
        [DueDate],
        [ShipDate],
        [DocNumber],
        [PrivateNote],
        [CustomerMemo],
        [BillEmail],
        [TotalAmt],
        [Balance],
        [Deposit],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [ClassRefValue],
        [ClassRefName],
        [ShipMethodRefValue],
        [ShipMethodRefName],
        [TrackingNum],
        [PrintStatus],
        [EmailStatus],
        [AllowOnlineACHPayment],
        [AllowOnlineCreditCardPayment],
        [ApplyTaxAfterDiscount],
        [GlobalTaxCalculation]
    FROM [qbo].[Invoice]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboInvoiceByQboIdAndRealmId
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
        [CustomerRefValue],
        [CustomerRefName],
        [TxnDate],
        [DueDate],
        [ShipDate],
        [DocNumber],
        [PrivateNote],
        [CustomerMemo],
        [BillEmail],
        [TotalAmt],
        [Balance],
        [Deposit],
        [SalesTermRefValue],
        [SalesTermRefName],
        [CurrencyRefValue],
        [CurrencyRefName],
        [ExchangeRate],
        [DepartmentRefValue],
        [DepartmentRefName],
        [ClassRefValue],
        [ClassRefName],
        [ShipMethodRefValue],
        [ShipMethodRefName],
        [TrackingNum],
        [PrintStatus],
        [EmailStatus],
        [AllowOnlineACHPayment],
        [AllowOnlineCreditCardPayment],
        [ApplyTaxAfterDiscount],
        [GlobalTaxCalculation]
    FROM [qbo].[Invoice]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboInvoiceByQboId
(
    @QboId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @CustomerRefValue NVARCHAR(50),
    @CustomerRefName NVARCHAR(500),
    @TxnDate NVARCHAR(50),
    @DueDate NVARCHAR(50),
    @ShipDate NVARCHAR(50),
    @DocNumber NVARCHAR(21),
    @PrivateNote NVARCHAR(4000),
    @CustomerMemo NVARCHAR(4000),
    @BillEmail NVARCHAR(500),
    @TotalAmt DECIMAL(18,2),
    @Balance DECIMAL(18,2),
    @Deposit DECIMAL(18,2),
    @SalesTermRefValue NVARCHAR(50),
    @SalesTermRefName NVARCHAR(500),
    @CurrencyRefValue NVARCHAR(10),
    @CurrencyRefName NVARCHAR(100),
    @ExchangeRate DECIMAL(18,6),
    @DepartmentRefValue NVARCHAR(50),
    @DepartmentRefName NVARCHAR(500),
    @ClassRefValue NVARCHAR(50),
    @ClassRefName NVARCHAR(500),
    @ShipMethodRefValue NVARCHAR(50),
    @ShipMethodRefName NVARCHAR(500),
    @TrackingNum NVARCHAR(500),
    @PrintStatus NVARCHAR(50),
    @EmailStatus NVARCHAR(50),
    @AllowOnlineACHPayment BIT,
    @AllowOnlineCreditCardPayment BIT,
    @ApplyTaxAfterDiscount BIT,
    @GlobalTaxCalculation NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[Invoice]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = @SyncToken,
        [RealmId] = @RealmId,
        [CustomerRefValue] = @CustomerRefValue,
        [CustomerRefName] = @CustomerRefName,
        [TxnDate] = @TxnDate,
        [DueDate] = @DueDate,
        [ShipDate] = @ShipDate,
        [DocNumber] = @DocNumber,
        [PrivateNote] = @PrivateNote,
        [CustomerMemo] = @CustomerMemo,
        [BillEmail] = @BillEmail,
        [TotalAmt] = @TotalAmt,
        [Balance] = @Balance,
        [Deposit] = @Deposit,
        [SalesTermRefValue] = @SalesTermRefValue,
        [SalesTermRefName] = @SalesTermRefName,
        [CurrencyRefValue] = @CurrencyRefValue,
        [CurrencyRefName] = @CurrencyRefName,
        [ExchangeRate] = @ExchangeRate,
        [DepartmentRefValue] = @DepartmentRefValue,
        [DepartmentRefName] = @DepartmentRefName,
        [ClassRefValue] = @ClassRefValue,
        [ClassRefName] = @ClassRefName,
        [ShipMethodRefValue] = @ShipMethodRefValue,
        [ShipMethodRefName] = @ShipMethodRefName,
        [TrackingNum] = @TrackingNum,
        [PrintStatus] = @PrintStatus,
        [EmailStatus] = @EmailStatus,
        [AllowOnlineACHPayment] = @AllowOnlineACHPayment,
        [AllowOnlineCreditCardPayment] = @AllowOnlineCreditCardPayment,
        [ApplyTaxAfterDiscount] = @ApplyTaxAfterDiscount,
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
        INSERTED.[CustomerRefValue],
        INSERTED.[CustomerRefName],
        INSERTED.[TxnDate],
        INSERTED.[DueDate],
        INSERTED.[ShipDate],
        INSERTED.[DocNumber],
        INSERTED.[PrivateNote],
        INSERTED.[CustomerMemo],
        INSERTED.[BillEmail],
        INSERTED.[TotalAmt],
        INSERTED.[Balance],
        INSERTED.[Deposit],
        INSERTED.[SalesTermRefValue],
        INSERTED.[SalesTermRefName],
        INSERTED.[CurrencyRefValue],
        INSERTED.[CurrencyRefName],
        INSERTED.[ExchangeRate],
        INSERTED.[DepartmentRefValue],
        INSERTED.[DepartmentRefName],
        INSERTED.[ClassRefValue],
        INSERTED.[ClassRefName],
        INSERTED.[ShipMethodRefValue],
        INSERTED.[ShipMethodRefName],
        INSERTED.[TrackingNum],
        INSERTED.[PrintStatus],
        INSERTED.[EmailStatus],
        INSERTED.[AllowOnlineACHPayment],
        INSERTED.[AllowOnlineCreditCardPayment],
        INSERTED.[ApplyTaxAfterDiscount],
        INSERTED.[GlobalTaxCalculation]
    WHERE [QboId] = @QboId AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboInvoiceByQboId
(
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[Invoice]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboId],
        DELETED.[SyncToken],
        DELETED.[RealmId],
        DELETED.[CustomerRefValue],
        DELETED.[CustomerRefName],
        DELETED.[TxnDate],
        DELETED.[DueDate],
        DELETED.[ShipDate],
        DELETED.[DocNumber],
        DELETED.[PrivateNote],
        DELETED.[CustomerMemo],
        DELETED.[BillEmail],
        DELETED.[TotalAmt],
        DELETED.[Balance],
        DELETED.[Deposit],
        DELETED.[SalesTermRefValue],
        DELETED.[SalesTermRefName],
        DELETED.[CurrencyRefValue],
        DELETED.[CurrencyRefName],
        DELETED.[ExchangeRate],
        DELETED.[DepartmentRefValue],
        DELETED.[DepartmentRefName],
        DELETED.[ClassRefValue],
        DELETED.[ClassRefName],
        DELETED.[ShipMethodRefValue],
        DELETED.[ShipMethodRefName],
        DELETED.[TrackingNum],
        DELETED.[PrintStatus],
        DELETED.[EmailStatus],
        DELETED.[AllowOnlineACHPayment],
        DELETED.[AllowOnlineCreditCardPayment],
        DELETED.[ApplyTaxAfterDiscount],
        DELETED.[GlobalTaxCalculation]
    WHERE [QboId] = @QboId;

    COMMIT TRANSACTION;
END;
GO


-- InvoiceLine Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateQboInvoiceLine
(
    @QboInvoiceId BIGINT,
    @QboLineId NVARCHAR(50),
    @LineNum INT,
    @Description NVARCHAR(4000),
    @Amount DECIMAL(18,2),
    @DetailType NVARCHAR(50),
    @ItemRefValue NVARCHAR(50),
    @ItemRefName NVARCHAR(500),
    @ClassRefValue NVARCHAR(50),
    @ClassRefName NVARCHAR(500),
    @Qty DECIMAL(18,6),
    @UnitPrice DECIMAL(18,6),
    @TaxCodeRefValue NVARCHAR(50),
    @TaxCodeRefName NVARCHAR(500),
    @ServiceDate NVARCHAR(50),
    @DiscountRate DECIMAL(18,6),
    @DiscountAmt DECIMAL(18,6)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[InvoiceLine] (
        [CreatedDatetime], [ModifiedDatetime], [QboInvoiceId], [QboLineId], [LineNum],
        [Description], [Amount], [DetailType], [ItemRefValue], [ItemRefName],
        [ClassRefValue], [ClassRefName], [Qty], [UnitPrice],
        [TaxCodeRefValue], [TaxCodeRefName], [ServiceDate],
        [DiscountRate], [DiscountAmt]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboInvoiceId],
        INSERTED.[QboLineId],
        INSERTED.[LineNum],
        INSERTED.[Description],
        INSERTED.[Amount],
        INSERTED.[DetailType],
        INSERTED.[ItemRefValue],
        INSERTED.[ItemRefName],
        INSERTED.[ClassRefValue],
        INSERTED.[ClassRefName],
        INSERTED.[Qty],
        INSERTED.[UnitPrice],
        INSERTED.[TaxCodeRefValue],
        INSERTED.[TaxCodeRefName],
        INSERTED.[ServiceDate],
        INSERTED.[DiscountRate],
        INSERTED.[DiscountAmt]
    VALUES (
        @Now, @Now, @QboInvoiceId, @QboLineId, @LineNum,
        @Description, @Amount, @DetailType, @ItemRefValue, @ItemRefName,
        @ClassRefValue, @ClassRefName, @Qty, @UnitPrice,
        @TaxCodeRefValue, @TaxCodeRefName, @ServiceDate,
        @DiscountRate, @DiscountAmt
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboInvoiceLinesByQboInvoiceId
(
    @QboInvoiceId BIGINT
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
        [QboInvoiceId],
        [QboLineId],
        [LineNum],
        [Description],
        [Amount],
        [DetailType],
        [ItemRefValue],
        [ItemRefName],
        [ClassRefValue],
        [ClassRefName],
        [Qty],
        [UnitPrice],
        [TaxCodeRefValue],
        [TaxCodeRefName],
        [ServiceDate],
        [DiscountRate],
        [DiscountAmt]
    FROM [qbo].[InvoiceLine]
    WHERE [QboInvoiceId] = @QboInvoiceId
    ORDER BY [LineNum] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadQboInvoiceLineByQboInvoiceIdAndQboLineId
(
    @QboInvoiceId BIGINT,
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
        [QboInvoiceId],
        [QboLineId],
        [LineNum],
        [Description],
        [Amount],
        [DetailType],
        [ItemRefValue],
        [ItemRefName],
        [ClassRefValue],
        [ClassRefName],
        [Qty],
        [UnitPrice],
        [TaxCodeRefValue],
        [TaxCodeRefName],
        [ServiceDate],
        [DiscountRate],
        [DiscountAmt]
    FROM [qbo].[InvoiceLine]
    WHERE [QboInvoiceId] = @QboInvoiceId AND [QboLineId] = @QboLineId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateQboInvoiceLineById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @LineNum INT,
    @Description NVARCHAR(4000),
    @Amount DECIMAL(18,2),
    @DetailType NVARCHAR(50),
    @ItemRefValue NVARCHAR(50),
    @ItemRefName NVARCHAR(500),
    @ClassRefValue NVARCHAR(50),
    @ClassRefName NVARCHAR(500),
    @Qty DECIMAL(18,6),
    @UnitPrice DECIMAL(18,6),
    @TaxCodeRefValue NVARCHAR(50),
    @TaxCodeRefName NVARCHAR(500),
    @ServiceDate NVARCHAR(50),
    @DiscountRate DECIMAL(18,6),
    @DiscountAmt DECIMAL(18,6)
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    UPDATE [qbo].[InvoiceLine]
    SET
        [ModifiedDatetime] = @Now,
        [LineNum] = @LineNum,
        [Description] = @Description,
        [Amount] = @Amount,
        [DetailType] = @DetailType,
        [ItemRefValue] = @ItemRefValue,
        [ItemRefName] = @ItemRefName,
        [ClassRefValue] = @ClassRefValue,
        [ClassRefName] = @ClassRefName,
        [Qty] = @Qty,
        [UnitPrice] = @UnitPrice,
        [TaxCodeRefValue] = @TaxCodeRefValue,
        [TaxCodeRefName] = @TaxCodeRefName,
        [ServiceDate] = @ServiceDate,
        [DiscountRate] = @DiscountRate,
        [DiscountAmt] = @DiscountAmt
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboInvoiceId],
        INSERTED.[QboLineId],
        INSERTED.[LineNum],
        INSERTED.[Description],
        INSERTED.[Amount],
        INSERTED.[DetailType],
        INSERTED.[ItemRefValue],
        INSERTED.[ItemRefName],
        INSERTED.[ClassRefValue],
        INSERTED.[ClassRefName],
        INSERTED.[Qty],
        INSERTED.[UnitPrice],
        INSERTED.[TaxCodeRefValue],
        INSERTED.[TaxCodeRefName],
        INSERTED.[ServiceDate],
        INSERTED.[DiscountRate],
        INSERTED.[DiscountAmt]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteQboInvoiceLineById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[InvoiceLine]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboInvoiceId],
        DELETED.[QboLineId],
        DELETED.[LineNum],
        DELETED.[Description],
        DELETED.[Amount],
        DELETED.[DetailType],
        DELETED.[ItemRefValue],
        DELETED.[ItemRefName],
        DELETED.[ClassRefValue],
        DELETED.[ClassRefName],
        DELETED.[Qty],
        DELETED.[UnitPrice],
        DELETED.[TaxCodeRefValue],
        DELETED.[TaxCodeRefName],
        DELETED.[ServiceDate],
        DELETED.[DiscountRate],
        DELETED.[DiscountAmt]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
