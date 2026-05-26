-- Migration 003 — add `SourceEmailMessageId` to Bill Read/Update/Delete sproc
-- projections.
--
-- Context: 2026-05-19 manual backlog walk surfaced that
-- BillRepository.read_by_id returned `source_email_message_id=None` for
-- Bill 18545 even though the underlying row had `SourceEmailMessageId=752`
-- (just-written by CreateBill seconds earlier). Root cause: ReadBillById
-- — and by extension every sibling Read* / OUTPUT projection — never
-- included the column. CreateBill wrote it; nothing else read it back.
-- Bill dataclass had the field (with a comment acknowledging the gap);
-- _from_db used getattr defensively. Pure additive fix:
-- include the column in 8 sproc projections in entities/bill/sql/dbo.bill.sql.
--
-- Why a migration file: dbo.bill.sql has a pre-existing
-- BillCompletionResult.ExpiresAt sproc breakage (TODO.md line 196) that
-- blocks re-running the canonical file. Same workaround as the
-- LinkBillSourceEmailMessage migration (dbo.bill_create_source_email.sql).
--
-- Idempotent: CREATE OR ALTER on each sproc. Re-applying is safe.
GO


-- ─── ReadBills ─────────────────────────────────────────────────────────
CREATE OR ALTER PROCEDURE ReadBills
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId]
    FROM dbo.[Bill]
    ORDER BY [BillDate] DESC, [BillNumber] ASC;

    COMMIT TRANSACTION;
END;
GO


-- ─── ReadBillById ──────────────────────────────────────────────────────
CREATE OR ALTER PROCEDURE ReadBillById
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
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId]
    FROM dbo.[Bill]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- ─── ReadBillByPublicId ────────────────────────────────────────────────
CREATE OR ALTER PROCEDURE ReadBillByPublicId
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
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId]
    FROM dbo.[Bill]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


-- ─── ReadBillByBillNumber ──────────────────────────────────────────────
CREATE OR ALTER PROCEDURE ReadBillByBillNumber
(
    @BillNumber NVARCHAR(50)
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
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber;

    COMMIT TRANSACTION;
END;
GO


-- ─── ReadBillByBillNumberAndVendorId ───────────────────────────────────
CREATE OR ALTER PROCEDURE ReadBillByBillNumberAndVendorId
(
    @BillNumber NVARCHAR(50),
    @VendorId BIGINT,
    @BillDate DATETIME2(3) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IntakeSource],
        [IntakeSourceDetail],
        [SourceEmailMessageId]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber
      AND [VendorId] = @VendorId
      AND (@BillDate IS NULL OR [BillDate] = @BillDate);

    COMMIT TRANSACTION;
END;
GO


-- ─── UpdateBillById (OUTPUT) ───────────────────────────────────────────
CREATE OR ALTER PROCEDURE UpdateBillById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @PaymentTermId BIGINT NULL,
    @BillDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @BillNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- IntakeSource / IntakeSourceDetail are set-once at create. The UPDATE
    -- statement deliberately omits them so existing values are preserved.
    UPDATE dbo.[Bill]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [PaymentTermId] = @PaymentTermId,
        [BillDate] = @BillDate,
        [DueDate] = @DueDate,
        [BillNumber] = @BillNumber,
        [TotalAmount] = @TotalAmount,
        [Memo] = @Memo,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
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
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


-- ─── DeleteBillById (OUTPUT) ───────────────────────────────────────────
CREATE OR ALTER PROCEDURE DeleteBillById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Bill]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[PaymentTermId],
        CONVERT(VARCHAR(19), DELETED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), DELETED.[DueDate], 120) AS [DueDate],
        DELETED.[BillNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft],
        DELETED.[IntakeSource],
        DELETED.[IntakeSourceDetail],
        DELETED.[SourceEmailMessageId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- ─── ReadBillsPaginated ────────────────────────────────────────────────
CREATE OR ALTER PROCEDURE ReadBillsPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @SortBy NVARCHAR(50) = 'BillDate',
    @SortDirection NVARCHAR(4) = 'DESC'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;

    DECLARE @SortColumn NVARCHAR(50);
    SET @SortColumn = CASE @SortBy
        WHEN 'BillNumber' THEN 'BillNumber'
        WHEN 'BillDate' THEN 'BillDate'
        WHEN 'DueDate' THEN 'DueDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'BillDate'
    END;

    DECLARE @SortDir NVARCHAR(4);
    SET @SortDir = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    SELECT
        b.[Id],
        b.[PublicId],
        b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[VendorId],
        b.[PaymentTermId],
        CONVERT(VARCHAR(19), b.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), b.[DueDate], 120) AS [DueDate],
        b.[BillNumber],
        b.[TotalAmount],
        b.[Memo],
        b.[IsDraft],
        b.[IntakeSource],
        b.[IntakeSourceDetail],
        b.[SourceEmailMessageId]
    FROM dbo.[Bill] b
    LEFT JOIN dbo.[Vendor] v ON b.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         b.[BillNumber] LIKE '%' + @SearchTerm + '%' OR
         b.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), b.[BillDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), b.[DueDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), b.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR b.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR b.[BillDate] >= @StartDate)
        AND (@EndDate IS NULL OR b.[BillDate] <= @EndDate)
        AND (@IsDraft IS NULL OR b.[IsDraft] = @IsDraft)
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'BillNumber' THEN b.[BillNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'BillNumber' THEN b.[BillNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'BillDate' THEN b.[BillDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'BillDate' THEN b.[BillDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'DueDate' THEN b.[DueDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'DueDate' THEN b.[DueDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN b.[TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN b.[TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'VendorId' THEN b.[VendorId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'VendorId' THEN b.[VendorId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    COMMIT TRANSACTION;
END;
GO
