-- Extend CreateBill to accept @SourceEmailMessageId so the email-agent
-- pipeline can stamp the source email FK on a draft bill at creation
-- time. NULL is the default — manual UI / API callers don't need to
-- pass it. Idempotent (CREATE OR ALTER).

GO

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
    @SourceEmailMessageId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Bill]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [PaymentTermId],
         [BillDate], [DueDate], [BillNumber], [TotalAmount], [Memo],
         [IsDraft], [IntakeSource], [IntakeSourceDetail], [SourceEmailMessageId])
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
            @IntakeSourceDetail, @SourceEmailMessageId);

    COMMIT TRANSACTION;
END;
GO
