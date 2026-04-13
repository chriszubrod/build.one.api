GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ExpenseLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ExpenseId] BIGINT NOT NULL,
    [SubCostCodeId] BIGINT NULL,
    [ProjectId] BIGINT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Quantity] INT NULL,
    [Rate] DECIMAL(18,4) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [IsBillable] BIT NULL,
    [IsBilled] BIT NULL,
    [Markup] DECIMAL(18,4) NULL,
    [Price] DECIMAL(18,2) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_ExpenseLineItem_Expense] FOREIGN KEY ([ExpenseId]) REFERENCES [dbo].[Expense]([Id]),
    CONSTRAINT [FK_ExpenseLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [FK_ExpenseLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_ExpenseId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_ExpenseId ON [dbo].[ExpenseLineItem] ([ExpenseId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_SubCostCodeId ON [dbo].[ExpenseLineItem] ([SubCostCodeId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_ProjectId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_ProjectId ON [dbo].[ExpenseLineItem] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_PublicId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_PublicId ON [dbo].[ExpenseLineItem] ([PublicId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateExpenseLineItem
(
    @ExpenseId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity INT NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ExpenseLineItem] ([CreatedDatetime], [ModifiedDatetime], [ExpenseId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [IsBilled], [Markup], [Price], [IsDraft])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ExpenseId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[IsBilled],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @ExpenseId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @IsBilled, @Markup, @Price, @IsDraft);

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItems
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[ExpenseLineItem]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemById
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
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[ExpenseLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
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
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[ExpenseLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemsByExpenseId
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
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[ExpenseLineItem]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateExpenseLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ExpenseId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity INT NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ExpenseLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [ExpenseId] = @ExpenseId,
        [SubCostCodeId] = CASE WHEN @SubCostCodeId IS NULL THEN [SubCostCodeId] ELSE @SubCostCodeId END,
        [ProjectId] = CASE WHEN @ProjectId IS NULL THEN [ProjectId] ELSE @ProjectId END,
        [Description] = @Description,
        [Quantity] = @Quantity,
        [Rate] = @Rate,
        [Amount] = @Amount,
        [IsBillable] = @IsBillable,
        [IsBilled] = @IsBilled,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ExpenseId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[IsBilled],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE DeleteExpenseLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ExpenseLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ExpenseId],
        DELETED.[SubCostCodeId],
        DELETED.[ProjectId],
        DELETED.[Description],
        DELETED.[Quantity],
        DELETED.[Rate],
        DELETED.[Amount],
        DELETED.[IsBillable],
        DELETED.[IsBilled],
        DELETED.[Markup],
        DELETED.[Price],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
