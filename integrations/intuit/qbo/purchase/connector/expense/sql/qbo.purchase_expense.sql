GO

IF OBJECT_ID('qbo.PurchaseExpense', 'U') IS NULL
BEGIN
CREATE TABLE [qbo].[PurchaseExpense]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [QboPurchaseId] BIGINT NOT NULL,
    [ExpenseId] BIGINT NOT NULL,
    CONSTRAINT [UQ_PurchaseExpense_QboPurchaseId] UNIQUE ([QboPurchaseId]),
    CONSTRAINT [UQ_PurchaseExpense_ExpenseId] UNIQUE ([ExpenseId])
);
END
GO


GO

CREATE OR ALTER PROCEDURE CreatePurchaseExpense
(
    @QboPurchaseId BIGINT,
    @ExpenseId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    BEGIN TRANSACTION;

    INSERT INTO [qbo].[PurchaseExpense] ([CreatedDatetime], [ModifiedDatetime], [QboPurchaseId], [ExpenseId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboPurchaseId],
        INSERTED.[ExpenseId]
    VALUES (@Now, @Now, @QboPurchaseId, @ExpenseId);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadPurchaseExpenseById
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
        [QboPurchaseId],
        [ExpenseId]
    FROM [qbo].[PurchaseExpense]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadPurchaseExpenseByExpenseId
(
    @ExpenseId BIGINT
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
        [ExpenseId]
    FROM [qbo].[PurchaseExpense]
    WHERE [ExpenseId] = @ExpenseId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadPurchaseExpenseByQboPurchaseId
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
        [ExpenseId]
    FROM [qbo].[PurchaseExpense]
    WHERE [QboPurchaseId] = @QboPurchaseId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeletePurchaseExpenseById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM [qbo].[PurchaseExpense]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[QboPurchaseId],
        DELETED.[ExpenseId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
