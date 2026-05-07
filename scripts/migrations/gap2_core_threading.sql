-- =====================================================================
-- Gap 2 Phase Core — thread CreatedByUserId on the 11 transactional
-- money-entity Create sprocs (Project + Bill family + Expense family +
-- Invoice family + ContractLabor family).
--
-- Pattern: add @CreatedByUserId BIGINT = NULL param; INSERT uses
-- COALESCE(@CreatedByUserId, 17) so the existing DEFAULT-trick fallback
-- still fires when callers don't pass an actor (scheduler / recovery
-- jobs / agents that haven't been threaded yet keep working).
--
-- Idempotent (CREATE OR ALTER). Migration-only — does NOT replay the
-- base sproc files (which would roll back later migrations like Gap 1
-- list-path filters or Phase 3 actor params on Read sprocs).
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ===== 1. CreateProject =====
CREATE OR ALTER PROCEDURE CreateProject
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(500),
    @Status NVARCHAR(50),
    @CustomerId BIGINT NULL,
    @Abbreviation NVARCHAR(20) NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Project] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [Status], [CustomerId], [Abbreviation], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[Status],
        INSERTED.[CustomerId],
        INSERTED.[Abbreviation]
    VALUES (@Now, @Now, @Name, @Description, @Status, @CustomerId, @Abbreviation, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 2. CreateBill =====
CREATE OR ALTER PROCEDURE CreateBill
(
    @VendorId BIGINT = NULL,
    @PaymentTermId BIGINT = NULL,
    @BillDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @BillNumber NVARCHAR(50) = NULL,
    @TotalAmount DECIMAL(18,2) = NULL,
    @Memo NVARCHAR(MAX) = NULL,
    @IsDraft BIT = 1,
    @IntakeSource NVARCHAR(20) = NULL,
    @IntakeSourceDetail NVARCHAR(100) = NULL,
    @SourceEmailMessageId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Bill]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [PaymentTermId],
         [BillDate], [DueDate], [BillNumber], [TotalAmount], [Memo],
         [IsDraft], [IntakeSource], [IntakeSourceDetail], [SourceEmailMessageId],
         [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[BillNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft],
        INSERTED.[IntakeSource],
        INSERTED.[IntakeSourceDetail],
        INSERTED.[SourceEmailMessageId]
    VALUES (@Now, @Now, @VendorId, @PaymentTermId, @BillDate, @DueDate,
            @BillNumber, @TotalAmount, @Memo, @IsDraft, @IntakeSource,
            @IntakeSourceDetail, @SourceEmailMessageId,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 3. CreateBillCredit =====
CREATE OR ALTER PROCEDURE CreateBillCredit
(
    @VendorId BIGINT,
    @CreditDate DATETIME2(3),
    @CreditNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillCredit] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [CreditDate], [CreditNumber], [TotalAmount], [Memo], [IsDraft], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        CONVERT(VARCHAR(19), INSERTED.[CreditDate], 120) AS [CreditDate],
        INSERTED.[CreditNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @VendorId, @CreditDate, @CreditNumber, @TotalAmount, @Memo, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 4. CreateExpense =====
CREATE OR ALTER PROCEDURE CreateExpense
(
    @VendorId BIGINT,
    @ExpenseDate DATETIME2(3),
    @ReferenceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1,
    @IsCredit BIT = 0,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Expense] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ExpenseDate], [ReferenceNumber], [TotalAmount], [Memo], [IsDraft], [IsCredit], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        CONVERT(VARCHAR(19), INSERTED.[ExpenseDate], 120) AS [ExpenseDate],
        INSERTED.[ReferenceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft],
        INSERTED.[IsCredit]
    VALUES (@Now, @Now, @VendorId, @ExpenseDate, @ReferenceNumber, @TotalAmount, @Memo, @IsDraft, @IsCredit, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 5. CreateInvoice =====
CREATE OR ALTER PROCEDURE CreateInvoice
(
    @ProjectId BIGINT,
    @PaymentTermId BIGINT NULL,
    @InvoiceDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @InvoiceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Invoice] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [PaymentTermId], [InvoiceDate], [DueDate], [InvoiceNumber], [TotalAmount], [Memo], [IsDraft], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[InvoiceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @ProjectId, @PaymentTermId, @InvoiceDate, @DueDate, @InvoiceNumber, @TotalAmount, @Memo, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 6. CreateContractLabor =====
CREATE OR ALTER PROCEDURE CreateContractLabor
(
    @VendorId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @EmployeeName NVARCHAR(255),
    @JobName NVARCHAR(255) NULL,
    @WorkDate DATE,
    @TimeIn NVARCHAR(20) NULL,
    @TimeOut NVARCHAR(20) NULL,
    @BreakTime NVARCHAR(20) NULL,
    @RegularHours DECIMAL(6,2) NULL,
    @OvertimeHours DECIMAL(6,2) NULL,
    @TotalHours DECIMAL(6,2),
    @HourlyRate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @TotalAmount DECIMAL(18,2) NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @BillingPeriodStart DATE NULL,
    @Status NVARCHAR(20) = 'pending_review',
    @BillLineItemId BIGINT NULL,
    @ImportBatchId NVARCHAR(50) NULL,
    @SourceFile NVARCHAR(255) NULL,
    @SourceRow INT NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ContractLabor] (
        [CreatedDatetime], [ModifiedDatetime], [VendorId], [ProjectId], [EmployeeName], [JobName],
        [WorkDate], [TimeIn], [TimeOut], [BreakTime], [RegularHours], [OvertimeHours],
        [TotalHours], [HourlyRate], [Markup], [TotalAmount], [SubCostCodeId], [Description],
        [BillingPeriodStart], [Status], [BillLineItemId], [ImportBatchId], [SourceFile], [SourceRow],
        [CreatedByUserId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[EmployeeName],
        INSERTED.[JobName],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[TimeIn],
        INSERTED.[TimeOut],
        INSERTED.[BreakTime],
        INSERTED.[RegularHours],
        INSERTED.[OvertimeHours],
        INSERTED.[TotalHours],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[TotalAmount],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        INSERTED.[Status],
        INSERTED.[BillLineItemId],
        INSERTED.[BillVendorId],
        CONVERT(VARCHAR(10), INSERTED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(10), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[BillNumber],
        INSERTED.[ImportBatchId],
        INSERTED.[SourceFile],
        INSERTED.[SourceRow]
    VALUES (
        @Now, @Now, @VendorId, @ProjectId, @EmployeeName, @JobName,
        @WorkDate, @TimeIn, @TimeOut, @BreakTime, @RegularHours, @OvertimeHours,
        @TotalHours, @HourlyRate, @Markup, @TotalAmount, @SubCostCodeId, @Description,
        @BillingPeriodStart, @Status, @BillLineItemId, @ImportBatchId, @SourceFile, @SourceRow,
        COALESCE(@CreatedByUserId, 17)
    );

    COMMIT TRANSACTION;
END;
GO

-- ===== 7. CreateBillLineItem =====
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

-- ===== 8. CreateBillCreditLineItem =====
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
    @IsBilled BIT NULL,
    @BillableAmount DECIMAL(18,2) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillCreditLineItem] ([CreatedDatetime], [ModifiedDatetime], [BillCreditId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [UnitPrice], [Amount], [IsBillable], [IsBilled], [BillableAmount], [IsDraft], [CreatedByUserId])
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
        INSERTED.[IsBilled],
        INSERTED.[BillableAmount],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @BillCreditId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @UnitPrice, @Amount, @IsBillable, @IsBilled, @BillableAmount, @IsDraft, COALESCE(@CreatedByUserId, 17));

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
    @Quantity INT NULL,
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

-- ===== 10. CreateInvoiceLineItem =====
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
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[InvoiceLineItem] ([CreatedDatetime], [ModifiedDatetime], [InvoiceId], [SourceType], [BillLineItemId], [ExpenseLineItemId], [BillCreditLineItemId], [SubCostCodeId], [Description], [Quantity], [Rate], [Amount], [Markup], [Price], [IsDraft], [CreatedByUserId])
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
    VALUES (@Now, @Now, @InvoiceId, @SourceType, @BillLineItemId, @ExpenseLineItemId, @BillCreditLineItemId, @SubCostCodeId, @Description, @Quantity, @Rate, @Amount, @Markup, @Price, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- ===== 11. CreateContractLaborLineItem =====
CREATE OR ALTER PROCEDURE CreateContractLaborLineItem
(
    @ContractLaborId BIGINT,
    @LineDate DATE NULL,
    @ProjectId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Hours DECIMAL(6,2) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsBillable BIT = 1,
    @IsOverhead BIT = 0,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ContractLaborLineItem] (
        [CreatedDatetime], [ModifiedDatetime], [ContractLaborId], [LineDate], [ProjectId], [SubCostCodeId],
        [Description], [Hours], [Rate], [Markup], [Price], [IsBillable], [IsOverhead], [CreatedByUserId]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ContractLaborId],
        CONVERT(VARCHAR(10), INSERTED.[LineDate], 120) AS [LineDate],
        INSERTED.[ProjectId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Hours],
        INSERTED.[Rate],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsBillable],
        INSERTED.[IsOverhead]
    VALUES (
        @Now, @Now, @ContractLaborId, @LineDate, @ProjectId, @SubCostCodeId,
        @Description, @Hours, @Rate, @Markup, @Price, @IsBillable, @IsOverhead,
        COALESCE(@CreatedByUserId, 17)
    );

    COMMIT TRANSACTION;
END;
GO

PRINT 'Gap 2 Phase Core: 11 Create sprocs threaded with @CreatedByUserId';
GO
