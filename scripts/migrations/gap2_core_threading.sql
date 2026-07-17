-- =====================================================================
-- Gap 2 Phase Core — thread CreatedByUserId on the transactional
-- money-entity Create sprocs whose entity base files do not yet carry it.
--
-- Pattern: add @CreatedByUserId BIGINT = NULL param; INSERT uses
-- COALESCE(@CreatedByUserId, 17) so the existing DEFAULT-trick fallback
-- still fires when callers do not pass an actor (scheduler / recovery
-- jobs / agents that have not been threaded yet keep working).
--
-- Idempotent (CREATE OR ALTER). Migration-only — does NOT replay the
-- base sproc files (which would roll back later migrations like Gap 1
-- list-path filters or Phase 3 actor params on Read sprocs).
--
-- U-061 (2026-07-17): 4 sprocs originally defined here — CreateProject,
-- CreateBill, CreateExpense, CreateInvoiceLineItem — were NEUTRALIZED to
-- base-canonical pointer stubs (see each section) because their bodies had
-- drifted BEHIND their entity base files; re-running them reverted prod.
-- The remaining 7 bodies are the live @CreatedByUserId threading.
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ===== 1. CreateProject =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/project/sql/dbo.project.sql
-- That base carries @CreatedByUserId (the original intent of this file) AND the
-- @Notes param this copy had drifted behind.
--
-- Drift: this body omitted @Notes. The repo layer sends @Notes unconditionally,
-- so re-running this file reverted prod CreateProject to the pre-@Notes shape and
-- broke project creation with SQL 8144 ("too many arguments") from ~2026-05-26
-- until the base was re-applied to prod on 2026-07-17. Re-running this file is
-- now a no-op for CreateProject. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 2. CreateBill =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill/sql/dbo.bill_create_source_email.sql
-- That base carries @CreatedByUserId (the original intent of this file).
--
-- Drift (body-level, params match): this body inserted the caller @DueDate,
-- whereas the base deliberately mirrors DueDate = @BillDate (migration
-- 005_bill_duedate_mirror_billdate). Because the params match, re-running this
-- file would NOT error — it would SILENTLY revert the DueDate = BillDate business
-- rule. Re-running this file is now a no-op for CreateBill. Do NOT reintroduce a
-- body here.
-- ---------------------------------------------------------------------------
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
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/expense/sql/dbo.expense.sql
-- That base carries @CreatedByUserId (the original intent of this file) AND the
-- @SourceEmailMessageId param this copy had drifted behind.
--
-- Drift: this body omitted @SourceEmailMessageId. ExpenseRepository.create sends
-- that param unconditionally (entities/expense/persistence/repo.py), so re-running
-- this file would revert prod CreateExpense to the pre-source-email shape and break
-- expense creation with SQL 8144 — the exact CreateProject failure mode. Re-running
-- this file is now a no-op for CreateExpense. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
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
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-074, 2026-07-17) - body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill_line_item/sql/dbo.bill_line_item.sql
-- That base carries @CreatedByUserId (this copy threading intent) AND @Quantity
-- DECIMAL(18,4). This copy had drifted behind on @Quantity INT, which silently
-- truncated fractional quantities on insert (prod ran this INT body). Re-running
-- this file is now a no-op for CreateBillLineItem. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
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
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-074, 2026-07-17) - body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/expense_line_item/sql/dbo.expense_line_item.sql
-- That base carries @CreatedByUserId (this copy threading intent) AND @Quantity
-- DECIMAL(18,4). This copy had drifted behind on @Quantity INT, which silently
-- truncated fractional quantities on insert (prod ran this INT body). Re-running
-- this file is now a no-op for CreateExpenseLineItem. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 10. CreateInvoiceLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/invoice_line_item/sql/dbo.invoice_line_item.sql
-- That base carries @CreatedByUserId (the original intent of this file) AND the
-- @EmployeeLaborLineItemId param this copy had drifted behind.
--
-- Drift: this body omitted @EmployeeLaborLineItemId. The repo layer sends that
-- param (entities/invoice_line_item/persistence/repo.py), so re-running this file
-- would revert prod to the pre-employee-labor shape and break invoice-line creation
-- with SQL 8144 — the same drift that hid @EmployeeLaborLineItemId from prod through
-- incidents WVA-17 / WVA-18 (base made canonical 2026-07-06). Re-running this file is
-- now a no-op for CreateInvoiceLineItem. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
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

PRINT 'Gap 2 Phase Core: 7 live Create sprocs threaded with @CreatedByUserId; 4 neutralized to base-canonical pointer stubs (U-061)';
GO
