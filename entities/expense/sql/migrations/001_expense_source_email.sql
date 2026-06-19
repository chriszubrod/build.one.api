-- 001_expense_source_email.sql
-- Unit 1 (ExpenseAgent / BillAgent parity): thread SourceEmailMessageId through
-- dbo.CreateExpense so receipt-intake (email + folder) can preserve the
-- source-email audit trail, mirroring dbo.CreateBill (gap2_core_threading.sql).
--
-- The [SourceEmailMessageId] BIGINT NULL column + FK + index already exist on
-- dbo.Expense (entities/email_message/sql/dbo.source_email_message_fk.sql); this
-- migration only extends the sproc to accept, INSERT, and OUTPUT it. The gap2
-- @CreatedByUserId threading + COALESCE(@CreatedByUserId, 17) system-context
-- fallback are preserved verbatim. Idempotent (CREATE OR ALTER).
GO

CREATE OR ALTER PROCEDURE CreateExpense
(
    @VendorId BIGINT,
    @ExpenseDate DATETIME2(3),
    @ReferenceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1,
    @IsCredit BIT = 0,
    @SourceEmailMessageId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Expense]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ExpenseDate],
         [ReferenceNumber], [TotalAmount], [Memo], [IsDraft], [IsCredit],
         [SourceEmailMessageId], [CreatedByUserId])
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
        INSERTED.[IsCredit],
        INSERTED.[SourceEmailMessageId]
    VALUES (@Now, @Now, @VendorId, @ExpenseDate, @ReferenceNumber, @TotalAmount,
            @Memo, @IsDraft, @IsCredit, @SourceEmailMessageId,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO
