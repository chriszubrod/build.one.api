IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[InvoiceLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [InvoiceId] BIGINT NOT NULL,
    [SourceType] NVARCHAR(50) NOT NULL,
    [BillLineItemId] BIGINT NULL,
    [ExpenseLineItemId] BIGINT NULL,
    [BillCreditLineItemId] BIGINT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [Markup] DECIMAL(18,4) NULL,
    [Price] DECIMAL(18,2) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_InvoiceLineItem_Invoice] FOREIGN KEY ([InvoiceId]) REFERENCES [dbo].[Invoice]([Id]),
    CONSTRAINT [FK_InvoiceLineItem_BillLineItem] FOREIGN KEY ([BillLineItemId]) REFERENCES [dbo].[BillLineItem]([Id]),
    CONSTRAINT [FK_InvoiceLineItem_ExpenseLineItem] FOREIGN KEY ([ExpenseLineItemId]) REFERENCES [dbo].[ExpenseLineItem]([Id]),
    CONSTRAINT [FK_InvoiceLineItem_BillCreditLineItem] FOREIGN KEY ([BillCreditLineItemId]) REFERENCES [dbo].[BillCreditLineItem]([Id])
);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_InvoiceId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_InvoiceId ON [dbo].[InvoiceLineItem] ([InvoiceId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_BillLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_BillLineItemId ON [dbo].[InvoiceLineItem] ([BillLineItemId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_ExpenseLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_ExpenseLineItemId ON [dbo].[InvoiceLineItem] ([ExpenseLineItemId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_BillCreditLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_BillCreditLineItemId ON [dbo].[InvoiceLineItem] ([BillCreditLineItemId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_PublicId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
CREATE INDEX IX_InvoiceLineItem_PublicId ON [dbo].[InvoiceLineItem] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'SubCostCodeId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [SubCostCodeId] BIGINT NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'Quantity' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [Quantity] DECIMAL(18,4) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name = 'Rate' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [Rate] DECIMAL(18,4) NULL;
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItem_SubCostCode' AND parent_object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem] ADD CONSTRAINT [FK_InvoiceLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]);
END
GO

IF OBJECT_ID('dbo.InvoiceLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    CREATE INDEX IX_InvoiceLineItem_SubCostCodeId ON [dbo].[InvoiceLineItem] ([SubCostCodeId]);
END
GO


CREATE OR ALTER PROCEDURE CreateInvoiceLineItem
(
    @InvoiceId BIGINT,
    @SourceType NVARCHAR(50),
    @BillLineItemId BIGINT NULL,
    @ExpenseLineItemId BIGINT NULL,
    @BillCreditLineItemId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[InvoiceLineItem] ([CreatedDatetime], [ModifiedDatetime], [InvoiceId], [SourceType], [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceId],
        INSERTED.[SourceType],
        INSERTED.[BillLineItemId],
        INSERTED.[ExpenseLineItemId],
        INSERTED.[BillCreditLineItemId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @InvoiceId, @SourceType, @BillLineItemId, @ExpenseLineItemId, @BillCreditLineItemId, @SubCostCodeId, @Description, @Quantity, @Rate, @Amount, @Markup, @Price, @IsDraft);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItems
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType], [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft]
    FROM dbo.[InvoiceLineItem]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType], [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft]
    FROM dbo.[InvoiceLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType], [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft]
    FROM dbo.[InvoiceLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceLineItemsByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [InvoiceId], [SourceType], [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft]
    FROM dbo.[InvoiceLineItem]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateInvoiceLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @InvoiceId BIGINT,
    @SourceType NVARCHAR(50),
    @BillLineItemId BIGINT NULL,
    @ExpenseLineItemId BIGINT NULL,
    @BillCreditLineItemId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[InvoiceLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [InvoiceId] = @InvoiceId,
        [SourceType] = @SourceType,
        [BillLineItemId] = @BillLineItemId,
        [ExpenseLineItemId] = @ExpenseLineItemId,
        [BillCreditLineItemId] = @BillCreditLineItemId,
        [SubCostCodeId] = @SubCostCodeId,
        [Description] = @Description,
        [Quantity] = @Quantity,
        [Rate] = @Rate,
        [Amount] = @Amount,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceId], INSERTED.[SourceType],
        INSERTED.[BillLineItemId], INSERTED.[ExpenseLineItemId], INSERTED.[BillCreditLineItemId],
        INSERTED.[SubCostCodeId], INSERTED.[Description], INSERTED.[Quantity], INSERTED.[Rate],
        INSERTED.[Amount], INSERTED.[Markup], INSERTED.[Price], INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE NullifyInvoiceLineItemsByBillLineItemId
(
    @BillLineItemId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[InvoiceLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillLineItemId] = NULL
    WHERE [BillLineItemId] = @BillLineItemId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteInvoiceLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[InvoiceLineItem]
    OUTPUT
        DELETED.[Id], DELETED.[PublicId], DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[InvoiceId], DELETED.[SourceType],
        DELETED.[BillLineItemId], DELETED.[ExpenseLineItemId], DELETED.[BillCreditLineItemId],
        DELETED.[SubCostCodeId], DELETED.[Description], DELETED.[Quantity], DELETED.[Rate],
        DELETED.[Amount], DELETED.[Markup], DELETED.[Price], DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
