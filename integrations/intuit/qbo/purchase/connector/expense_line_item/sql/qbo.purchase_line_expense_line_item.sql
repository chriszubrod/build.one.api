GO

IF OBJECT_ID('qbo.PurchaseLineExpenseLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[PurchaseLineExpenseLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboPurchaseLineId] BIGINT NOT NULL,
    [ExpenseLineItemId] BIGINT NOT NULL,
    CONSTRAINT [UQ_PurchaseLineExpenseLineItem_QboPurchaseLineId] UNIQUE ([QboPurchaseLineId]),
    CONSTRAINT [UQ_PurchaseLineExpenseLineItem_ExpenseLineItemId] UNIQUE ([ExpenseLineItemId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreatePurchaseLineExpenseLineItem
(
    @QboPurchaseLineId BIGINT,
    @ExpenseLineItemId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[PurchaseLineExpenseLineItem] ([CreatedDatetime], [ModifiedDatetime], [QboPurchaseLineId], [ExpenseLineItemId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboPurchaseLineId],
        INSERTED.[ExpenseLineItemId]
    VALUES (@Now, @Now, @QboPurchaseLineId, @ExpenseLineItemId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadPurchaseLineExpenseLineItemById
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
        [QboPurchaseLineId],
        [ExpenseLineItemId]
    FROM [qbo].[PurchaseLineExpenseLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadPurchaseLineExpenseLineItemByExpenseLineItemId
(
    @ExpenseLineItemId BIGINT
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
        [QboPurchaseLineId],
        [ExpenseLineItemId]
    FROM [qbo].[PurchaseLineExpenseLineItem]
    WHERE [ExpenseLineItemId] = @ExpenseLineItemId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadPurchaseLineExpenseLineItemByQboPurchaseLineId
(
    @QboPurchaseLineId BIGINT
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
        [QboPurchaseLineId],
        [ExpenseLineItemId]
    FROM [qbo].[PurchaseLineExpenseLineItem]
    WHERE [QboPurchaseLineId] = @QboPurchaseLineId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeletePurchaseLineExpenseLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[PurchaseLineExpenseLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboPurchaseLineId],
        DELETED.[ExpenseLineItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
