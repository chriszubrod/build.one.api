-- =============================================================================
-- 2026-05-27 — Phase 3: Invoice source for EmployeeLabor.
--
-- Adds InvoiceLineItem.EmployeeLaborLineItemId nullable FK so the polymorphic
-- source set grows from {Bill, Expense, BillCredit, Manual} to add
-- {EmployeeLabor}. SourceType has no CHECK constraint today so the new value
-- 'EmployeeLabor' is purely a Python convention — no constraint to update.
--
-- Re-issues Create/Read/Update sprocs with the new column threaded through.
-- DeleteInvoiceLineItemById OUTPUT NOT re-issued — its returned shape is
-- only used to confirm deletion; missing the new column doesn't break callers.
--
-- Idempotent. Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- Column addition + FK + index --------------------------------------------------
IF COL_LENGTH('dbo.[InvoiceLineItem]', 'EmployeeLaborLineItemId') IS NULL
    ALTER TABLE [dbo].[InvoiceLineItem] ADD [EmployeeLaborLineItemId] BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_InvoiceLineItem_EmployeeLaborLineItem')
   AND OBJECT_ID('dbo.[EmployeeLaborLineItem]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[InvoiceLineItem]
    ADD CONSTRAINT [FK_InvoiceLineItem_EmployeeLaborLineItem]
        FOREIGN KEY ([EmployeeLaborLineItemId]) REFERENCES [dbo].[EmployeeLaborLineItem]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_InvoiceLineItem_EmployeeLaborLineItemId' AND object_id = OBJECT_ID('dbo.InvoiceLineItem'))
BEGIN
    CREATE INDEX [IX_InvoiceLineItem_EmployeeLaborLineItemId]
        ON [dbo].[InvoiceLineItem] ([EmployeeLaborLineItemId]);
END
GO


-- Re-issue sprocs with the new column ------------------------------------------
CREATE OR ALTER PROCEDURE CreateInvoiceLineItem
(
    @InvoiceId BIGINT,
    @SourceType NVARCHAR(50),
    @BillLineItemId BIGINT NULL,
    @ExpenseLineItemId BIGINT NULL,
    @BillCreditLineItemId BIGINT NULL,
    @EmployeeLaborLineItemId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[InvoiceLineItem]
        ([CreatedDatetime], [ModifiedDatetime], [InvoiceId], [SourceType],
         [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
         [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft],
         [CreatedByUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[InvoiceId], INSERTED.[SourceType],
        INSERTED.[BillLineItemId], INSERTED.[ExpenseLineItemId], INSERTED.[BillCreditLineItemId],
        INSERTED.[EmployeeLaborLineItemId],
        INSERTED.[SubCostCodeId], INSERTED.[Description], INSERTED.[Quantity], INSERTED.[Rate],
        INSERTED.[Amount], INSERTED.[Markup], INSERTED.[Price], INSERTED.[IsDraft]
    VALUES (@Now, @Now, @InvoiceId, @SourceType,
            @BillLineItemId, @ExpenseLineItemId, @BillCreditLineItemId, @EmployeeLaborLineItemId,
            @SubCostCodeId, @Description, @Quantity, @Rate, @Amount, @Markup, @Price, @IsDraft,
            COALESCE(@CreatedByUserId, 17));

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
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
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
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
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
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
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
        [InvoiceId], [SourceType],
        [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [EmployeeLaborLineItemId],
        [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft]
    FROM dbo.[InvoiceLineItem]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] ASC;
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
    @EmployeeLaborLineItemId BIGINT NULL,
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

    -- Source FKs use CASE WHEN preserve-on-NULL so partial updates don't
    -- orphan the link to the source line. Same pattern as existing source
    -- columns.
    UPDATE dbo.[InvoiceLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [InvoiceId] = @InvoiceId,
        [SourceType] = @SourceType,
        [BillLineItemId]          = CASE WHEN @BillLineItemId          IS NULL THEN [BillLineItemId]          ELSE @BillLineItemId          END,
        [ExpenseLineItemId]       = CASE WHEN @ExpenseLineItemId       IS NULL THEN [ExpenseLineItemId]       ELSE @ExpenseLineItemId       END,
        [BillCreditLineItemId]    = CASE WHEN @BillCreditLineItemId    IS NULL THEN [BillCreditLineItemId]    ELSE @BillCreditLineItemId    END,
        [EmployeeLaborLineItemId] = CASE WHEN @EmployeeLaborLineItemId IS NULL THEN [EmployeeLaborLineItemId] ELSE @EmployeeLaborLineItemId END,
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
        INSERTED.[EmployeeLaborLineItemId],
        INSERTED.[SubCostCodeId], INSERTED.[Description], INSERTED.[Quantity], INSERTED.[Rate],
        INSERTED.[Amount], INSERTED.[Markup], INSERTED.[Price], INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

PRINT 'InvoiceLineItem EmployeeLabor source migration applied.';
