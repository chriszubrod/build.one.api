DROP TABLE IF EXISTS dbo.[BillLineItem];
GO

CREATE TABLE [dbo].[BillLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillId] BIGINT NOT NULL,
    [SubCostCodeId] BIGINT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Quantity] INT NULL,
    [Rate] DECIMAL(18,4) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [IsBillable] BIT NULL,
    [Markup] DECIMAL(18,4) NULL,
    [Price] DECIMAL(18,2) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_BillLineItem_Bill] FOREIGN KEY ([BillId]) REFERENCES [dbo].[Bill]([Id]),
    CONSTRAINT [FK_BillLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id])
);
GO

CREATE INDEX IX_BillLineItem_BillId ON [dbo].[BillLineItem] ([BillId]);
GO

CREATE INDEX IX_BillLineItem_SubCostCodeId ON [dbo].[BillLineItem] ([SubCostCodeId]);
GO

CREATE INDEX IX_BillLineItem_PublicId ON [dbo].[BillLineItem] ([PublicId]);
GO

DROP PROCEDURE IF EXISTS CreateBillLineItem;
GO

CREATE PROCEDURE CreateBillLineItem
(
    @BillId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity INT NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillLineItem] ([CreatedDatetime], [ModifiedDatetime], [BillId], [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [Markup], [Price], [IsDraft])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @BillId, @SubCostCodeId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @Markup, @Price, @IsDraft);

    COMMIT TRANSACTION;
END;

EXEC CreateBillLineItem
    @BillId = 1,
    @SubCostCodeId = NULL,
    @Description = 'Sample bill line item',
    @Quantity = 10,
    @Rate = 50.00,
    @IsBillable = 1,
    @Markup = 0.10,
    @IsDraft = 0;
GO

DROP PROCEDURE IF EXISTS ReadBillLineItems;
GO

CREATE PROCEDURE ReadBillLineItems
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BillId],
        [SubCostCodeId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[BillLineItem]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItems;
GO

DROP PROCEDURE IF EXISTS ReadBillLineItemById;
GO

CREATE PROCEDURE ReadBillLineItemById
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
        [BillId],
        [SubCostCodeId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[BillLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItemById
    @Id = 1;
GO

DROP PROCEDURE IF EXISTS ReadBillLineItemByPublicId;
GO

CREATE PROCEDURE ReadBillLineItemByPublicId
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
        [BillId],
        [SubCostCodeId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[BillLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItemByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

DROP PROCEDURE IF EXISTS ReadBillLineItemsByBillId;
GO

CREATE PROCEDURE ReadBillLineItemsByBillId
(
    @BillId BIGINT
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
        [BillId],
        [SubCostCodeId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[BillLineItem]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;

EXEC ReadBillLineItemsByBillId
    @BillId = 1;
GO

DROP PROCEDURE IF EXISTS UpdateBillLineItemById;
GO

CREATE PROCEDURE UpdateBillLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @BillId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity INT NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillId] = @BillId,
        [SubCostCodeId] = @SubCostCodeId,
        [Description] = @Description,
        [Quantity] = @Quantity,
        [Rate] = @Rate,
        [Amount] = @Amount,
        [IsBillable] = @IsBillable,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateBillLineItemById
    @Id = 1,
    @RowVersion = 0x0000000000020B74,
    @BillId = 1,
    @SubCostCodeId = NULL,
    @Description = 'Updated bill line item',
    @Quantity = 15,
    @Rate = 60.00,
    @IsBillable = 1,
    @Markup = 0.15;
GO

DROP PROCEDURE IF EXISTS DeleteBillLineItemById;
GO

CREATE PROCEDURE DeleteBillLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[BillLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillId],
        DELETED.[SubCostCodeId],
        DELETED.[Description],
        DELETED.[Quantity],
        DELETED.[Rate],
        DELETED.[Amount],
        DELETED.[IsBillable],
        DELETED.[Markup],
        DELETED.[Price],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteBillLineItemById
    @Id = 1;
GO

SELECT * FROM dbo.BillLineItem;