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

-- Non-unique index on QboId for fast single-field lookups
IF OBJECT_ID('qbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboInvoice_QboId' AND object_id = OBJECT_ID('qbo.Invoice'))
BEGIN
CREATE INDEX IX_QboInvoice_QboId ON [qbo].[Invoice] ([QboId]);
END
GO

-- Unique constraint: one row per QBO invoice per company realm
IF OBJECT_ID('qbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_QboInvoice_QboId_RealmId' AND object_id = OBJECT_ID('qbo.Invoice'))
BEGIN
CREATE UNIQUE INDEX UQ_QboInvoice_QboId_RealmId ON [qbo].[Invoice] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL AND [RealmId] IS NOT NULL;
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

-- Unique constraint: one row per QBO line ID per QBO invoice
IF OBJECT_ID('qbo.InvoiceLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_QboInvoiceLine_QboInvoiceId_QboLineId' AND object_id = OBJECT_ID('qbo.InvoiceLine'))
BEGIN
CREATE UNIQUE INDEX UQ_QboInvoiceLine_QboInvoiceId_QboLineId ON [qbo].[InvoiceLine] ([QboInvoiceId], [QboLineId]) WHERE [QboLineId] IS NOT NULL;
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
        [SyncToken] = CASE WHEN @SyncToken IS NULL THEN [SyncToken] ELSE @SyncToken END,
        [RealmId] = CASE WHEN @RealmId IS NULL THEN [RealmId] ELSE @RealmId END,
        [CustomerRefValue] = CASE WHEN @CustomerRefValue IS NULL THEN [CustomerRefValue] ELSE @CustomerRefValue END,
        [CustomerRefName] = CASE WHEN @CustomerRefName IS NULL THEN [CustomerRefName] ELSE @CustomerRefName END,
        [TxnDate] = CASE WHEN @TxnDate IS NULL THEN [TxnDate] ELSE @TxnDate END,
        [DueDate] = CASE WHEN @DueDate IS NULL THEN [DueDate] ELSE @DueDate END,
        [ShipDate] = CASE WHEN @ShipDate IS NULL THEN [ShipDate] ELSE @ShipDate END,
        [DocNumber] = CASE WHEN @DocNumber IS NULL THEN [DocNumber] ELSE @DocNumber END,
        [PrivateNote] = CASE WHEN @PrivateNote IS NULL THEN [PrivateNote] ELSE @PrivateNote END,
        [CustomerMemo] = CASE WHEN @CustomerMemo IS NULL THEN [CustomerMemo] ELSE @CustomerMemo END,
        [BillEmail] = CASE WHEN @BillEmail IS NULL THEN [BillEmail] ELSE @BillEmail END,
        [TotalAmt] = CASE WHEN @TotalAmt IS NULL THEN [TotalAmt] ELSE @TotalAmt END,
        [Balance] = CASE WHEN @Balance IS NULL THEN [Balance] ELSE @Balance END,
        [Deposit] = CASE WHEN @Deposit IS NULL THEN [Deposit] ELSE @Deposit END,
        [SalesTermRefValue] = CASE WHEN @SalesTermRefValue IS NULL THEN [SalesTermRefValue] ELSE @SalesTermRefValue END,
        [SalesTermRefName] = CASE WHEN @SalesTermRefName IS NULL THEN [SalesTermRefName] ELSE @SalesTermRefName END,
        [CurrencyRefValue] = CASE WHEN @CurrencyRefValue IS NULL THEN [CurrencyRefValue] ELSE @CurrencyRefValue END,
        [CurrencyRefName] = CASE WHEN @CurrencyRefName IS NULL THEN [CurrencyRefName] ELSE @CurrencyRefName END,
        [ExchangeRate] = CASE WHEN @ExchangeRate IS NULL THEN [ExchangeRate] ELSE @ExchangeRate END,
        [DepartmentRefValue] = CASE WHEN @DepartmentRefValue IS NULL THEN [DepartmentRefValue] ELSE @DepartmentRefValue END,
        [DepartmentRefName] = CASE WHEN @DepartmentRefName IS NULL THEN [DepartmentRefName] ELSE @DepartmentRefName END,
        [ClassRefValue] = CASE WHEN @ClassRefValue IS NULL THEN [ClassRefValue] ELSE @ClassRefValue END,
        [ClassRefName] = CASE WHEN @ClassRefName IS NULL THEN [ClassRefName] ELSE @ClassRefName END,
        [ShipMethodRefValue] = CASE WHEN @ShipMethodRefValue IS NULL THEN [ShipMethodRefValue] ELSE @ShipMethodRefValue END,
        [ShipMethodRefName] = CASE WHEN @ShipMethodRefName IS NULL THEN [ShipMethodRefName] ELSE @ShipMethodRefName END,
        [TrackingNum] = CASE WHEN @TrackingNum IS NULL THEN [TrackingNum] ELSE @TrackingNum END,
        [PrintStatus] = CASE WHEN @PrintStatus IS NULL THEN [PrintStatus] ELSE @PrintStatus END,
        [EmailStatus] = CASE WHEN @EmailStatus IS NULL THEN [EmailStatus] ELSE @EmailStatus END,
        [AllowOnlineACHPayment] = CASE WHEN @AllowOnlineACHPayment IS NULL THEN [AllowOnlineACHPayment] ELSE @AllowOnlineACHPayment END,
        [AllowOnlineCreditCardPayment] = CASE WHEN @AllowOnlineCreditCardPayment IS NULL THEN [AllowOnlineCreditCardPayment] ELSE @AllowOnlineCreditCardPayment END,
        [ApplyTaxAfterDiscount] = CASE WHEN @ApplyTaxAfterDiscount IS NULL THEN [ApplyTaxAfterDiscount] ELSE @ApplyTaxAfterDiscount END,
        [GlobalTaxCalculation] = CASE WHEN @GlobalTaxCalculation IS NULL THEN [GlobalTaxCalculation] ELSE @GlobalTaxCalculation END
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
        [LineNum] = CASE WHEN @LineNum IS NULL THEN [LineNum] ELSE @LineNum END,
        [Description] = CASE WHEN @Description IS NULL THEN [Description] ELSE @Description END,
        [Amount] = CASE WHEN @Amount IS NULL THEN [Amount] ELSE @Amount END,
        [DetailType] = CASE WHEN @DetailType IS NULL THEN [DetailType] ELSE @DetailType END,
        [ItemRefValue] = CASE WHEN @ItemRefValue IS NULL THEN [ItemRefValue] ELSE @ItemRefValue END,
        [ItemRefName] = CASE WHEN @ItemRefName IS NULL THEN [ItemRefName] ELSE @ItemRefName END,
        [ClassRefValue] = CASE WHEN @ClassRefValue IS NULL THEN [ClassRefValue] ELSE @ClassRefValue END,
        [ClassRefName] = CASE WHEN @ClassRefName IS NULL THEN [ClassRefName] ELSE @ClassRefName END,
        [Qty] = CASE WHEN @Qty IS NULL THEN [Qty] ELSE @Qty END,
        [UnitPrice] = CASE WHEN @UnitPrice IS NULL THEN [UnitPrice] ELSE @UnitPrice END,
        [TaxCodeRefValue] = CASE WHEN @TaxCodeRefValue IS NULL THEN [TaxCodeRefValue] ELSE @TaxCodeRefValue END,
        [TaxCodeRefName] = CASE WHEN @TaxCodeRefName IS NULL THEN [TaxCodeRefName] ELSE @TaxCodeRefName END,
        [ServiceDate] = CASE WHEN @ServiceDate IS NULL THEN [ServiceDate] ELSE @ServiceDate END,
        [DiscountRate] = CASE WHEN @DiscountRate IS NULL THEN [DiscountRate] ELSE @DiscountRate END,
        [DiscountAmt] = CASE WHEN @DiscountAmt IS NULL THEN [DiscountAmt] ELSE @DiscountAmt END
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
