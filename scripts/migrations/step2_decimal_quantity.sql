-- Step 2: line-item Quantity INT -> DECIMAL(18,4) (fractional qty support).
-- Columns widened + the 4 Create/Update sprocs re-issued from their LIVE definitions
-- (preserving gap2 @CreatedByUserId), with only @Quantity changed. Idempotent.

IF EXISTS (SELECT 1 FROM sys.columns col JOIN sys.types t ON col.user_type_id=t.user_type_id WHERE col.object_id=OBJECT_ID('dbo.BillLineItem') AND col.name='Quantity' AND t.name='int') ALTER TABLE dbo.[BillLineItem] ALTER COLUMN [Quantity] DECIMAL(18,4) NULL;
GO

IF EXISTS (SELECT 1 FROM sys.columns col JOIN sys.types t ON col.user_type_id=t.user_type_id WHERE col.object_id=OBJECT_ID('dbo.ExpenseLineItem') AND col.name='Quantity' AND t.name='int') ALTER TABLE dbo.[ExpenseLineItem] ALTER COLUMN [Quantity] DECIMAL(18,4) NULL;
GO

-- ===== 7. CreateBillLineItem =====
CREATE OR ALTER PROCEDURE CreateBillLineItem
(
    @BillId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillLineItem] ([CreatedDatetime], [ModifiedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [IsBilled], [Markup], [Price], [IsDraft], [CreatedByUserId])
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
    VALUES (@Now, @Now, @BillId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @IsBilled, @Markup, @Price, @IsDraft, COALESCE(@CreatedByUserId, 17));

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
    @Quantity DECIMAL(18,4) NULL,
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

-- ===== 9. CreateExpenseLineItem =====
CREATE OR ALTER PROCEDURE CreateExpenseLineItem
(
    @ExpenseId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ExpenseLineItem] ([CreatedDatetime], [ModifiedDatetime], [ExpenseId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [IsBilled], [Markup], [Price], [IsDraft], [CreatedByUserId])
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
    VALUES (@Now, @Now, @ExpenseId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @IsBilled, @Markup, @Price, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE UpdateExpenseLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ExpenseId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
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
