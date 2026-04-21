-- QBO VendorCredit cache table
GO
GO

IF OBJECT_ID('qbo.VendorCredit', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[VendorCredit]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [RealmId] NVARCHAR(50) NOT NULL,
    [QboId] NVARCHAR(50) NOT NULL,
    [SyncToken] NVARCHAR(50) NULL,
    [VendorRefValue] NVARCHAR(50) NULL,
    [VendorRefName] NVARCHAR(255) NULL,
    [TxnDate] DATETIME2(3) NULL,
    [DocNumber] NVARCHAR(50) NULL,
    [TotalAmt] DECIMAL(18,2) NULL,
    [PrivateNote] NVARCHAR(MAX) NULL,
    [APAccountRefValue] NVARCHAR(50) NULL,
    [APAccountRefName] NVARCHAR(255) NULL,
    [CurrencyRefValue] NVARCHAR(10) NULL,
    [CurrencyRefName] NVARCHAR(50) NULL
);
END
GO

IF OBJECT_ID('qbo.VendorCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboVendorCredit_QboId' AND object_id = OBJECT_ID('qbo.VendorCredit'))
BEGIN
CREATE INDEX IX_QboVendorCredit_QboId ON [qbo].[VendorCredit] ([QboId]);
END
GO

IF OBJECT_ID('qbo.VendorCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboVendorCredit_RealmId' AND object_id = OBJECT_ID('qbo.VendorCredit'))
BEGIN
CREATE INDEX IX_QboVendorCredit_RealmId ON [qbo].[VendorCredit] ([RealmId]);
END
GO

IF OBJECT_ID('qbo.VendorCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_QboVendorCredit_QboId_RealmId' AND object_id = OBJECT_ID('qbo.VendorCredit'))
BEGIN
CREATE UNIQUE INDEX UQ_QboVendorCredit_QboId_RealmId ON [qbo].[VendorCredit] ([QboId], [RealmId]);
END
GO

-- VendorCredit Line items table
IF OBJECT_ID('qbo.VendorCreditLine', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[VendorCreditLine]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboVendorCreditId] BIGINT NOT NULL,
    [QboLineId] NVARCHAR(50) NULL,
    [LineNum] INT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [DetailType] NVARCHAR(50) NULL,
    -- Item-based fields
    [ItemRefValue] NVARCHAR(50) NULL,
    [ItemRefName] NVARCHAR(255) NULL,
    [ClassRefValue] NVARCHAR(50) NULL,
    [ClassRefName] NVARCHAR(255) NULL,
    [UnitPrice] DECIMAL(18,4) NULL,
    [Qty] DECIMAL(18,4) NULL,
    [BillableStatus] NVARCHAR(50) NULL,
    [CustomerRefValue] NVARCHAR(50) NULL,
    [CustomerRefName] NVARCHAR(255) NULL,
    -- Account-based fields
    [AccountRefValue] NVARCHAR(50) NULL,
    [AccountRefName] NVARCHAR(255) NULL,
    CONSTRAINT [FK_QboVendorCreditLine_QboVendorCredit] FOREIGN KEY ([QboVendorCreditId]) REFERENCES [qbo].[VendorCredit]([Id]) ON DELETE CASCADE
);
END
GO

IF OBJECT_ID('qbo.VendorCreditLine', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_QboVendorCreditLine_QboVendorCreditId' AND object_id = OBJECT_ID('qbo.VendorCreditLine'))
BEGIN
CREATE INDEX IX_QboVendorCreditLine_QboVendorCreditId ON [qbo].[VendorCreditLine] ([QboVendorCreditId]);
END
GO

-- Stored Procedures
GO

CREATE OR ALTER PROCEDURE CreateQboVendorCredit
(
    @RealmId NVARCHAR(50),
    @QboId NVARCHAR(50),
    @SyncToken NVARCHAR(50) NULL,
    @VendorRefValue NVARCHAR(50) NULL,
    @VendorRefName NVARCHAR(255) NULL,
    @TxnDate DATETIME2(3) NULL,
    @DocNumber NVARCHAR(50) NULL,
    @TotalAmt DECIMAL(18,2) NULL,
    @PrivateNote NVARCHAR(MAX) NULL,
    @APAccountRefValue NVARCHAR(50) NULL,
    @APAccountRefName NVARCHAR(255) NULL,
    @CurrencyRefValue NVARCHAR(10) NULL,
    @CurrencyRefName NVARCHAR(50) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    
    INSERT INTO [qbo].[VendorCredit] (
        [CreatedDatetime], [ModifiedDatetime], [RealmId], [QboId], [SyncToken],
        [VendorRefValue], [VendorRefName], [TxnDate], [DocNumber], [TotalAmt],
        [PrivateNote], [APAccountRefValue], [APAccountRefName], [CurrencyRefValue], [CurrencyRefName]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RealmId], INSERTED.[QboId], INSERTED.[SyncToken],
        INSERTED.[VendorRefValue], INSERTED.[VendorRefName],
        CONVERT(VARCHAR(19), INSERTED.[TxnDate], 120) AS [TxnDate],
        INSERTED.[DocNumber], INSERTED.[TotalAmt], INSERTED.[PrivateNote],
        INSERTED.[APAccountRefValue], INSERTED.[APAccountRefName],
        INSERTED.[CurrencyRefValue], INSERTED.[CurrencyRefName]
    VALUES (
        @Now, @Now, @RealmId, @QboId, @SyncToken,
        @VendorRefValue, @VendorRefName, @TxnDate, @DocNumber, @TotalAmt,
        @PrivateNote, @APAccountRefValue, @APAccountRefName, @CurrencyRefValue, @CurrencyRefName
    );
    
    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadQboVendorCreditByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RealmId], [QboId], [SyncToken],
        [VendorRefValue], [VendorRefName],
        CONVERT(VARCHAR(19), [TxnDate], 120) AS [TxnDate],
        [DocNumber], [TotalAmt], [PrivateNote],
        [APAccountRefValue], [APAccountRefName],
        [CurrencyRefValue], [CurrencyRefName]
    FROM [qbo].[VendorCredit]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadQboVendorCreditsByRealmId
(
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RealmId], [QboId], [SyncToken],
        [VendorRefValue], [VendorRefName],
        CONVERT(VARCHAR(19), [TxnDate], 120) AS [TxnDate],
        [DocNumber], [TotalAmt], [PrivateNote],
        [APAccountRefValue], [APAccountRefName],
        [CurrencyRefValue], [CurrencyRefName]
    FROM [qbo].[VendorCredit]
    WHERE [RealmId] = @RealmId
    ORDER BY [TxnDate] DESC;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadQboVendorCreditById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [RealmId], [QboId], [SyncToken],
        [VendorRefValue], [VendorRefName],
        CONVERT(VARCHAR(19), [TxnDate], 120) AS [TxnDate],
        [DocNumber], [TotalAmt], [PrivateNote],
        [APAccountRefValue], [APAccountRefName],
        [CurrencyRefValue], [CurrencyRefName]
    FROM [qbo].[VendorCredit]
    WHERE [Id] = @Id;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateQboVendorCreditByQboId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @RowVersion BINARY(8),
    @SyncToken NVARCHAR(50) NULL,
    @VendorRefValue NVARCHAR(50) NULL,
    @VendorRefName NVARCHAR(255) NULL,
    @TxnDate DATETIME2(3) NULL,
    @DocNumber NVARCHAR(50) NULL,
    @TotalAmt DECIMAL(18,2) NULL,
    @PrivateNote NVARCHAR(MAX) NULL,
    @APAccountRefValue NVARCHAR(50) NULL,
    @APAccountRefName NVARCHAR(255) NULL,
    @CurrencyRefValue NVARCHAR(10) NULL,
    @CurrencyRefName NVARCHAR(50) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    
    UPDATE [qbo].[VendorCredit]
    SET
        [ModifiedDatetime] = @Now,
        [SyncToken] = CASE WHEN @SyncToken IS NULL THEN [SyncToken] ELSE @SyncToken END,
        [VendorRefValue] = CASE WHEN @VendorRefValue IS NULL THEN [VendorRefValue] ELSE @VendorRefValue END,
        [VendorRefName] = CASE WHEN @VendorRefName IS NULL THEN [VendorRefName] ELSE @VendorRefName END,
        [TxnDate] = CASE WHEN @TxnDate IS NULL THEN [TxnDate] ELSE @TxnDate END,
        [DocNumber] = CASE WHEN @DocNumber IS NULL THEN [DocNumber] ELSE @DocNumber END,
        [TotalAmt] = CASE WHEN @TotalAmt IS NULL THEN [TotalAmt] ELSE @TotalAmt END,
        [PrivateNote] = CASE WHEN @PrivateNote IS NULL THEN [PrivateNote] ELSE @PrivateNote END,
        [APAccountRefValue] = CASE WHEN @APAccountRefValue IS NULL THEN [APAccountRefValue] ELSE @APAccountRefValue END,
        [APAccountRefName] = CASE WHEN @APAccountRefName IS NULL THEN [APAccountRefName] ELSE @APAccountRefName END,
        [CurrencyRefValue] = CASE WHEN @CurrencyRefValue IS NULL THEN [CurrencyRefValue] ELSE @CurrencyRefValue END,
        [CurrencyRefName] = CASE WHEN @CurrencyRefName IS NULL THEN [CurrencyRefName] ELSE @CurrencyRefName END
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[RealmId], INSERTED.[QboId], INSERTED.[SyncToken],
        INSERTED.[VendorRefValue], INSERTED.[VendorRefName],
        CONVERT(VARCHAR(19), INSERTED.[TxnDate], 120) AS [TxnDate],
        INSERTED.[DocNumber], INSERTED.[TotalAmt], INSERTED.[PrivateNote],
        INSERTED.[APAccountRefValue], INSERTED.[APAccountRefName],
        INSERTED.[CurrencyRefValue], INSERTED.[CurrencyRefName]
    WHERE [QboId] = @QboId AND [RealmId] = @RealmId AND [RowVersion] = @RowVersion;
    
    COMMIT TRANSACTION;
END;
GO

-- VendorCredit Line procedures
GO

CREATE OR ALTER PROCEDURE CreateQboVendorCreditLine
(
    @QboVendorCreditId BIGINT,
    @QboLineId NVARCHAR(50) NULL,
    @LineNum INT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Amount DECIMAL(18,2) NULL,
    @DetailType NVARCHAR(50) NULL,
    @ItemRefValue NVARCHAR(50) NULL,
    @ItemRefName NVARCHAR(255) NULL,
    @ClassRefValue NVARCHAR(50) NULL,
    @ClassRefName NVARCHAR(255) NULL,
    @UnitPrice DECIMAL(18,4) NULL,
    @Qty DECIMAL(18,4) NULL,
    @BillableStatus NVARCHAR(50) NULL,
    @CustomerRefValue NVARCHAR(50) NULL,
    @CustomerRefName NVARCHAR(255) NULL,
    @AccountRefValue NVARCHAR(50) NULL,
    @AccountRefName NVARCHAR(255) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    
    INSERT INTO [qbo].[VendorCreditLine] (
        [CreatedDatetime], [ModifiedDatetime], [QboVendorCreditId], [QboLineId], [LineNum],
        [Description], [Amount], [DetailType], [ItemRefValue], [ItemRefName],
        [ClassRefValue], [ClassRefName], [UnitPrice], [Qty], [BillableStatus],
        [CustomerRefValue], [CustomerRefName], [AccountRefValue], [AccountRefName]
    )
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboVendorCreditId], INSERTED.[QboLineId], INSERTED.[LineNum],
        INSERTED.[Description], INSERTED.[Amount], INSERTED.[DetailType],
        INSERTED.[ItemRefValue], INSERTED.[ItemRefName], INSERTED.[ClassRefValue], INSERTED.[ClassRefName],
        INSERTED.[UnitPrice], INSERTED.[Qty], INSERTED.[BillableStatus],
        INSERTED.[CustomerRefValue], INSERTED.[CustomerRefName],
        INSERTED.[AccountRefValue], INSERTED.[AccountRefName]
    VALUES (
        @Now, @Now, @QboVendorCreditId, @QboLineId, @LineNum,
        @Description, @Amount, @DetailType, @ItemRefValue, @ItemRefName,
        @ClassRefValue, @ClassRefName, @UnitPrice, @Qty, @BillableStatus,
        @CustomerRefValue, @CustomerRefName, @AccountRefValue, @AccountRefName
    );
    
    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadQboVendorCreditLinesByVendorCreditId
(
    @QboVendorCreditId BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboVendorCreditId], [QboLineId], [LineNum],
        [Description], [Amount], [DetailType],
        [ItemRefValue], [ItemRefName], [ClassRefValue], [ClassRefName],
        [UnitPrice], [Qty], [BillableStatus],
        [CustomerRefValue], [CustomerRefName],
        [AccountRefValue], [AccountRefName]
    FROM [qbo].[VendorCreditLine]
    WHERE [QboVendorCreditId] = @QboVendorCreditId
    ORDER BY [LineNum] ASC;
END;
GO

GO

CREATE OR ALTER PROCEDURE DeleteQboVendorCreditLinesByVendorCreditId
(
    @QboVendorCreditId BIGINT
)
AS
BEGIN
    DELETE FROM [qbo].[VendorCreditLine]
    WHERE [QboVendorCreditId] = @QboVendorCreditId;
END;
GO

-- Mapping table for VendorCredit <-> BillCredit
GO

IF OBJECT_ID('qbo.VendorCreditBillCredit', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[VendorCreditBillCredit]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboVendorCreditId] BIGINT NOT NULL,
    [BillCreditId] BIGINT NOT NULL,
    CONSTRAINT [FK_VendorCreditBillCredit_QboVendorCredit] FOREIGN KEY ([QboVendorCreditId]) REFERENCES [qbo].[VendorCredit]([Id]),
    CONSTRAINT [FK_VendorCreditBillCredit_BillCredit] FOREIGN KEY ([BillCreditId]) REFERENCES [dbo].[BillCredit]([Id]),
    CONSTRAINT [UQ_VendorCreditBillCredit_QboVendorCreditId] UNIQUE ([QboVendorCreditId]),
    CONSTRAINT [UQ_VendorCreditBillCredit_BillCreditId] UNIQUE ([BillCreditId])
);
END
GO

GO

CREATE OR ALTER PROCEDURE CreateVendorCreditBillCredit
(
    @QboVendorCreditId BIGINT,
    @BillCreditId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    
    INSERT INTO [qbo].[VendorCreditBillCredit] ([CreatedDatetime], [ModifiedDatetime], [QboVendorCreditId], [BillCreditId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboVendorCreditId], INSERTED.[BillCreditId]
    VALUES (@Now, @Now, @QboVendorCreditId, @BillCreditId);
    
    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadVendorCreditBillCreditByBillCreditId
(
    @BillCreditId BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboVendorCreditId], [BillCreditId]
    FROM [qbo].[VendorCreditBillCredit]
    WHERE [BillCreditId] = @BillCreditId;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadVendorCreditBillCreditByQboVendorCreditId
(
    @QboVendorCreditId BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboVendorCreditId], [BillCreditId]
    FROM [qbo].[VendorCreditBillCredit]
    WHERE [QboVendorCreditId] = @QboVendorCreditId;
END;
GO
