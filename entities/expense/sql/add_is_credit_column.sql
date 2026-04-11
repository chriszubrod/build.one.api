-- Migration: Add IsCredit column to dbo.Expense
-- Purpose: Distinguish CreditCardCredits (QBO Purchase with Credit=true) from regular Expenses
-- Run with: python scripts/run_sql.py entities/expense/sql/add_is_credit_column.sql

-- ============================================================================
-- 1. Add column
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Expense') AND name = 'IsCredit')
BEGIN
    ALTER TABLE [dbo].[Expense] ADD [IsCredit] BIT NOT NULL DEFAULT 0;
    PRINT 'Added IsCredit column to dbo.Expense';
END
ELSE
    PRINT 'IsCredit column already exists';
GO


-- ============================================================================
-- 2. Update stored procedures
-- ============================================================================

CREATE OR ALTER PROCEDURE CreateExpense
(
    @VendorId BIGINT,
    @ExpenseDate DATETIME2(3),
    @ReferenceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1,
    @IsCredit BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Expense] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ExpenseDate], [ReferenceNumber], [TotalAmount], [Memo], [IsDraft], [IsCredit])
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
    VALUES (@Now, @Now, @VendorId, @ExpenseDate, @ReferenceNumber, @TotalAmount, @Memo, @IsDraft, @IsCredit);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenses
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
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IsCredit]
    FROM dbo.[Expense]
    ORDER BY [ExpenseDate] DESC, [ReferenceNumber] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseById
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
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IsCredit]
    FROM dbo.[Expense]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseByPublicId
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
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IsCredit]
    FROM dbo.[Expense]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadExpenseByReferenceNumberAndVendorId
(
    @ReferenceNumber NVARCHAR(50),
    @VendorId BIGINT
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
        CONVERT(VARCHAR(19), [ExpenseDate], 120) AS [ExpenseDate],
        [ReferenceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft],
        [IsCredit]
    FROM dbo.[Expense]
    WHERE [ReferenceNumber] = @ReferenceNumber AND [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateExpenseById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @ExpenseDate DATETIME2(3),
    @ReferenceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = NULL,
    @IsCredit BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Expense]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [ExpenseDate] = @ExpenseDate,
        [ReferenceNumber] = @ReferenceNumber,
        [TotalAmount] = @TotalAmount,
        [Memo] = @Memo,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END,
        [IsCredit] = CASE WHEN @IsCredit IS NULL THEN [IsCredit] ELSE @IsCredit END
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
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteExpenseById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Expense]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        CONVERT(VARCHAR(19), DELETED.[ExpenseDate], 120) AS [ExpenseDate],
        DELETED.[ReferenceNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft],
        DELETED.[IsCredit]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- Pagination and filtering procedures
GO

CREATE OR ALTER PROCEDURE ReadExpensesPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @IsCredit BIT = NULL,
    @SortBy NVARCHAR(50) = 'ExpenseDate',
    @SortDirection NVARCHAR(4) = 'DESC'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;

    DECLARE @SortColumn NVARCHAR(50);
    SET @SortColumn = CASE @SortBy
        WHEN 'ReferenceNumber' THEN 'ReferenceNumber'
        WHEN 'ExpenseDate' THEN 'ExpenseDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'ExpenseDate'
    END;

    DECLARE @SortDir NVARCHAR(4);
    SET @SortDir = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    SELECT
        e.[Id],
        e.[PublicId],
        e.[RowVersion],
        CONVERT(VARCHAR(19), e.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), e.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        e.[VendorId],
        CONVERT(VARCHAR(19), e.[ExpenseDate], 120) AS [ExpenseDate],
        e.[ReferenceNumber],
        e.[TotalAmount],
        e.[Memo],
        e.[IsDraft],
        e.[IsCredit]
    FROM dbo.[Expense] e
    LEFT JOIN dbo.[Vendor] v ON e.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         e.[ReferenceNumber] LIKE '%' + @SearchTerm + '%' OR
         e.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), e.[ExpenseDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), e.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR e.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR e.[ExpenseDate] >= @StartDate)
        AND (@EndDate IS NULL OR e.[ExpenseDate] <= @EndDate)
        AND (@IsDraft IS NULL OR e.[IsDraft] = @IsDraft)
        AND (@IsCredit IS NULL OR e.[IsCredit] = @IsCredit)
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ReferenceNumber' THEN e.[ReferenceNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ReferenceNumber' THEN e.[ReferenceNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ExpenseDate' THEN e.[ExpenseDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ExpenseDate' THEN e.[ExpenseDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN e.[TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN e.[TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'VendorId' THEN e.[VendorId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'VendorId' THEN e.[VendorId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE CountExpenses
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @IsCredit BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[Expense] e
    LEFT JOIN dbo.[Vendor] v ON e.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         e.[ReferenceNumber] LIKE '%' + @SearchTerm + '%' OR
         e.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), e.[ExpenseDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), e.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR e.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR e.[ExpenseDate] >= @StartDate)
        AND (@EndDate IS NULL OR e.[ExpenseDate] <= @EndDate)
        AND (@IsDraft IS NULL OR e.[IsDraft] = @IsDraft)
        AND (@IsCredit IS NULL OR e.[IsCredit] = @IsCredit);

    COMMIT TRANSACTION;
END;
GO
