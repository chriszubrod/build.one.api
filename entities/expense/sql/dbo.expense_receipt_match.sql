-- Contract: dbo.ExpenseReceiptMatch   (U-716a; receipt drop match + attach sprocs)
-- Owns: ReadExpenseReceiptCandidates, AttachFirstReceiptToExpense (no tables).
-- Invariants: actor-scoped via dbo.UserCanAccessExpense; attach is U-468 add-only first receipt.
-- Sprocs: ReadExpenseReceiptCandidates, AttachFirstReceiptToExpense
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/expense/sql/dbo.expense_receipt_match.sql

GO

CREATE OR ALTER PROCEDURE dbo.ReadExpenseReceiptCandidates
(
    @TotalAmount DECIMAL(18, 2),
    @ExpenseDate DATE = NULL,
    @VendorId BIGINT = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Today DATE = CAST(SYSUTCDATETIME() AS DATE);
    DECLARE @DaysApartAnchor DATE = COALESCE(@ExpenseDate, @Today);

    SELECT TOP (10)
        e.[PublicId],
        v.[Name] AS [VendorName],
        e.[ExpenseDate],
        e.[TotalAmount],
        e.[ReferenceNumber],
        e.[IsDraft],
        e.[Status],
        CASE
            WHEN @VendorId IS NOT NULL AND e.[VendorId] = @VendorId THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END AS [VendorMatches],
        ABS(DATEDIFF(DAY, CAST(e.[ExpenseDate] AS DATE), @DaysApartAnchor)) AS [DaysApart]
    FROM dbo.[Expense] e
    LEFT JOIN dbo.[Vendor] v ON v.[Id] = e.[VendorId]
    WHERE dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1
      AND e.[TotalAmount] IN (@TotalAmount, -@TotalAmount)
      AND (
            (@ExpenseDate IS NOT NULL
             AND e.[ExpenseDate] >= DATEADD(DAY, -5, CAST(@ExpenseDate AS DATETIME2(3)))
             AND e.[ExpenseDate] < DATEADD(DAY, 6, CAST(@ExpenseDate AS DATETIME2(3))))
         OR (@ExpenseDate IS NULL
             AND e.[ExpenseDate] >= DATEADD(DAY, -120, @Today)
             AND e.[ExpenseDate] < DATEADD(DAY, 1, @Today))
          )
      AND EXISTS (
            SELECT 1 FROM dbo.[ExpenseLineItem] eli WHERE eli.[ExpenseId] = e.[Id]
          )
      AND NOT EXISTS (
            SELECT 1
            FROM dbo.[ExpenseLineItem] eli
            INNER JOIN dbo.[ExpenseLineItemAttachment] elia ON elia.[ExpenseLineItemId] = eli.[Id]
            WHERE eli.[ExpenseId] = e.[Id]
          )
    ORDER BY
        CASE WHEN @VendorId IS NOT NULL AND e.[VendorId] = @VendorId THEN 0 ELSE 1 END,
        ABS(DATEDIFF(DAY, CAST(e.[ExpenseDate] AS DATE), @DaysApartAnchor)),
        e.[Id] DESC;
END;
GO

-- U-468 add-only exception: this sproc adds the FIRST receipt to an expense that has
-- none on any line. It never replaces or removes attachments; completed expenses are
-- allowed only in this narrow case. All other write paths keep the terminal lock.
CREATE OR ALTER PROCEDURE dbo.AttachFirstReceiptToExpense
(
    @ExpensePublicId UNIQUEIDENTIFIER,
    @AttachmentId BIGINT,
    @CreatedByUserId BIGINT = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @ExpenseId BIGINT;
    DECLARE @LineItemId BIGINT;
    DECLARE @LineItemPublicId UNIQUEIDENTIFIER;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ResultCode NVARCHAR(32) = N'not_found';

    -- Attachment lock first (same order as CreateExpenseLineItemAttachment).
    DECLARE @LockedAttachment BIT;
    SELECT @LockedAttachment = 1
    FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)
    WHERE [Id] = @AttachmentId;

    IF @LockedAttachment IS NULL
    BEGIN
        COMMIT TRANSACTION;
        SELECT @ResultCode AS [ResultCode];
        RETURN;
    END

    -- Linked-anywhere tables: keep in sync with dbo.ReadAttachmentLinkState (see entities/attachment/sql/dbo.attachment_link_state.sql).
    IF EXISTS (SELECT 1 FROM dbo.[BillLineItemAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[ExpenseLineItemAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[BillCreditLineItemAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[InvoiceAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[InvoiceLineItemAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[BusinessLicenseAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[ContractorsLicenseAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[AssetAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM ms.[DriveItemAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[BillTextCodingSuggestion] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[CodingSuggestion] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM dbo.[CertificateOfInsurance] WHERE [AttachmentId] = @AttachmentId)
       OR EXISTS (SELECT 1 FROM box.[File] WHERE [AttachmentId] = @AttachmentId)
    BEGIN
        COMMIT TRANSACTION;
        SELECT N'attachment_linked' AS [ResultCode];
        RETURN;
    END

    SELECT @ExpenseId = e.[Id]
    FROM dbo.[Expense] e
    WHERE e.[PublicId] = @ExpensePublicId
      AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1;

    IF @ExpenseId IS NULL
    BEGIN
        COMMIT TRANSACTION;
        SELECT N'not_found' AS [ResultCode];
        RETURN;
    END

    -- Re-confirm under the lock: a delete that committed after the unhinted read must
    -- read as not_found, not fall through to expense_has_no_lines.
    DECLARE @LockedExpenseId BIGINT;
    SELECT @LockedExpenseId = e.[Id]
    FROM dbo.[Expense] e WITH (UPDLOCK, HOLDLOCK)
    WHERE e.[Id] = @ExpenseId;

    IF @LockedExpenseId IS NULL
    BEGIN
        COMMIT TRANSACTION;
        SELECT N'not_found' AS [ResultCode];
        RETURN;
    END

    IF EXISTS (
        SELECT 1
        FROM dbo.[ExpenseLineItem] eli
        INNER JOIN dbo.[ExpenseLineItemAttachment] elia WITH (UPDLOCK, HOLDLOCK)
            ON elia.[ExpenseLineItemId] = eli.[Id]
        WHERE eli.[ExpenseId] = @ExpenseId
    )
    BEGIN
        COMMIT TRANSACTION;
        SELECT N'expense_has_receipt' AS [ResultCode];
        RETURN;
    END

    SELECT TOP (1)
        @LineItemId = eli.[Id],
        @LineItemPublicId = eli.[PublicId]
    FROM dbo.[ExpenseLineItem] eli WITH (UPDLOCK, HOLDLOCK)
    WHERE eli.[ExpenseId] = @ExpenseId
    ORDER BY eli.[Id] ASC;

    IF @LineItemId IS NULL
    BEGIN
        COMMIT TRANSACTION;
        SELECT N'expense_has_no_lines' AS [ResultCode];
        RETURN;
    END

    INSERT INTO dbo.[ExpenseLineItemAttachment] (
        [CreatedDatetime],
        [ModifiedDatetime],
        [ExpenseLineItemId],
        [AttachmentId],
        [CreatedByUserId]
    )
    VALUES (
        @Now,
        @Now,
        @LineItemId,
        @AttachmentId,
        COALESCE(@CreatedByUserId, 17)
    );

    SET @ResultCode = N'ok';

    COMMIT TRANSACTION;

    SELECT
        @ResultCode AS [ResultCode],
        @ExpensePublicId AS [ExpensePublicId],
        @LineItemPublicId AS [ExpenseLineItemPublicId],
        a.[PublicId] AS [AttachmentPublicId]
    FROM dbo.[Attachment] a
    WHERE a.[Id] = @AttachmentId;
END;
GO
