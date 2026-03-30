IF OBJECT_ID('dbo.BillLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillId] BIGINT NOT NULL,
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
    CONSTRAINT [FK_BillLineItem_Bill] FOREIGN KEY ([BillId]) REFERENCES [dbo].[Bill]([Id]),
    CONSTRAINT [FK_BillLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [FK_BillLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_BillId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_BillId ON [dbo].[BillLineItem] ([BillId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_SubCostCodeId ON [dbo].[BillLineItem] ([SubCostCodeId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_ProjectId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_ProjectId ON [dbo].[BillLineItem] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_PublicId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_PublicId ON [dbo].[BillLineItem] ([PublicId]);
END
GO



CREATE OR ALTER PROCEDURE CreateBillLineItem
(
    @BillId BIGINT,
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

    INSERT INTO dbo.[BillLineItem] ([CreatedDatetime], [ModifiedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [IsBilled], [Markup], [Price], [IsDraft])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillId],
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
    VALUES (@Now, @Now, @BillId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @IsBilled, @Markup, @Price, @IsDraft);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadBillLineItems
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
    FROM dbo.[BillLineItem]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadBillLineItemById
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
    FROM dbo.[BillLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadBillLineItemByPublicId
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
    FROM dbo.[BillLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE ReadBillLineItemsByBillId
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
    FROM dbo.[BillLineItem]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE UpdateBillLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @BillId BIGINT,
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

    UPDATE dbo.[BillLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillId] = @BillId,
        [SubCostCodeId] = @SubCostCodeId,
        [ProjectId] = @ProjectId,
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
        INSERTED.[BillId],
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





CREATE OR ALTER PROCEDURE DeleteBillLineItemById
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



CREATE OR ALTER PROCEDURE ReadBillLineItemsByProjectId
(
    @ProjectId BIGINT
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
    FROM dbo.[BillLineItem]
    WHERE [ProjectId] = @ProjectId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


SELECT *
FROM dbo.Bill
WHERE PublicId = 'E2C0D6E2-3776-4933-B62D-723558F03BDF';
GO


SELECT *
FROM dbo.BillLineItem
WHERE BillId = 16968;
GO
