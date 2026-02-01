GO

IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillCreditLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillCreditId] BIGINT NOT NULL,
    [SubCostCodeId] BIGINT NULL,
    [ProjectId] BIGINT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Quantity] DECIMAL(18,4) NULL,
    [UnitPrice] DECIMAL(18,4) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [IsBillable] BIT NULL,
    [BillableAmount] DECIMAL(18,2) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_BillCreditLineItem_BillCredit] FOREIGN KEY ([BillCreditId]) REFERENCES [dbo].[BillCredit]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_BillCreditLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [FK_BillCreditLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO

IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillCreditLineItem_BillCreditId' AND object_id = OBJECT_ID('dbo.BillCreditLineItem'))
BEGIN
CREATE INDEX IX_BillCreditLineItem_BillCreditId ON [dbo].[BillCreditLineItem] ([BillCreditId]);
END
GO

IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillCreditLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.BillCreditLineItem'))
BEGIN
CREATE INDEX IX_BillCreditLineItem_SubCostCodeId ON [dbo].[BillCreditLineItem] ([SubCostCodeId]);
END
GO

IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillCreditLineItem_ProjectId' AND object_id = OBJECT_ID('dbo.BillCreditLineItem'))
BEGIN
CREATE INDEX IX_BillCreditLineItem_ProjectId ON [dbo].[BillCreditLineItem] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.BillCreditLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillCreditLineItem_PublicId' AND object_id = OBJECT_ID('dbo.BillCreditLineItem'))
BEGIN
CREATE INDEX IX_BillCreditLineItem_PublicId ON [dbo].[BillCreditLineItem] ([PublicId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateBillCreditLineItem
(
    @BillCreditId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @UnitPrice DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @BillableAmount DECIMAL(18,2) NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillCreditLineItem] ([CreatedDatetime], [ModifiedDatetime], [BillCreditId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [UnitPrice], [Amount], [IsBillable], [BillableAmount], [IsDraft])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillCreditId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[UnitPrice],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[BillableAmount],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @BillCreditId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @UnitPrice, @Amount, @IsBillable, @BillableAmount, @IsDraft);

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItems
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BillCreditId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [UnitPrice],
        [Amount],
        [IsBillable],
        [BillableAmount],
        [IsDraft]
    FROM dbo.[BillCreditLineItem]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItemById
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
        [BillCreditId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [UnitPrice],
        [Amount],
        [IsBillable],
        [BillableAmount],
        [IsDraft]
    FROM dbo.[BillCreditLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItemByPublicId
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
        [BillCreditId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [UnitPrice],
        [Amount],
        [IsBillable],
        [BillableAmount],
        [IsDraft]
    FROM dbo.[BillCreditLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCreditLineItemsByBillCreditId
(
    @BillCreditId BIGINT
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
        [BillCreditId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [UnitPrice],
        [Amount],
        [IsBillable],
        [BillableAmount],
        [IsDraft]
    FROM dbo.[BillCreditLineItem]
    WHERE [BillCreditId] = @BillCreditId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateBillCreditLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @BillCreditId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @UnitPrice DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @BillableAmount DECIMAL(18,2) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillCreditLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillCreditId] = @BillCreditId,
        [SubCostCodeId] = @SubCostCodeId,
        [ProjectId] = @ProjectId,
        [Description] = @Description,
        [Quantity] = @Quantity,
        [UnitPrice] = @UnitPrice,
        [Amount] = @Amount,
        [IsBillable] = @IsBillable,
        [BillableAmount] = @BillableAmount,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillCreditId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[UnitPrice],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[BillableAmount],
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE DeleteBillCreditLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[BillCreditLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillCreditId],
        DELETED.[SubCostCodeId],
        DELETED.[ProjectId],
        DELETED.[Description],
        DELETED.[Quantity],
        DELETED.[UnitPrice],
        DELETED.[Amount],
        DELETED.[IsBillable],
        DELETED.[BillableAmount],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
