-- Gap 1 perf fixup — replace UserCanAccessBill/BillCredit/Expense UDF
-- calls with inline EXISTS clauses on the Bill/BillCredit/Expense list
-- + paginated + count sprocs.
--
-- Why: the UDFs wrap an EXISTS in CONVERT(BIT, CASE) which appears to
-- prevent SQL Server's scalar UDF inlining (Froid). On 18K-row Bill
-- counts the UDF is called per-row → minutes-long queries. Inline
-- EXISTS lets the optimizer push it into a semi-join.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- The UDFs themselves stay in place (other future callers may use them);
-- this migration just removes them from the hot list/paginated/count path.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- =====================================================================
-- Bill
-- =====================================================================

CREATE OR ALTER PROCEDURE ReadBills
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
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
        b.[IntakeSourceDetail]
    FROM dbo.[Bill] b
    WHERE
        @ActorIsSystemAdmin = 1
        OR @ActorUserId IS NULL
        OR EXISTS (
            SELECT 1
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = bli.[ProjectId]
            WHERE bli.[BillId] = b.[Id] AND up.[UserId] = @ActorUserId
        )
    ORDER BY b.[BillDate] DESC, b.[BillNumber] ASC;
    COMMIT TRANSACTION;
END;
GO

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
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    DECLARE @SortColumn NVARCHAR(50) = CASE @SortBy
        WHEN 'BillNumber' THEN 'BillNumber'
        WHEN 'BillDate' THEN 'BillDate'
        WHEN 'DueDate' THEN 'DueDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'BillDate'
    END;
    DECLARE @SortDir NVARCHAR(4) = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

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
        b.[IntakeSourceDetail]
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
        AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] bli
                INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = bli.[ProjectId]
                WHERE bli.[BillId] = b.[Id] AND up.[UserId] = @ActorUserId
            )
        )
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

CREATE OR ALTER PROCEDURE CountBills
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT COUNT(*) AS [TotalCount]
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
        AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] bli
                INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = bli.[ProjectId]
                WHERE bli.[BillId] = b.[Id] AND up.[UserId] = @ActorUserId
            )
        );
    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- BillCredit
-- =====================================================================

CREATE OR ALTER PROCEDURE ReadBillCredits
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        bc.[Id],
        bc.[PublicId],
        bc.[RowVersion],
        CONVERT(VARCHAR(19), bc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), bc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        bc.[VendorId],
        CONVERT(VARCHAR(19), bc.[CreditDate], 120) AS [CreditDate],
        bc.[CreditNumber],
        bc.[TotalAmount],
        bc.[Memo],
        bc.[IsDraft]
    FROM dbo.[BillCredit] bc
    WHERE
        @ActorIsSystemAdmin = 1
        OR @ActorUserId IS NULL
        OR EXISTS (
            SELECT 1
            FROM dbo.[BillCreditLineItem] bcli
            INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = bcli.[ProjectId]
            WHERE bcli.[BillCreditId] = bc.[Id] AND up.[UserId] = @ActorUserId
        )
    ORDER BY bc.[CreditDate] DESC, bc.[CreditNumber] ASC;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadBillCreditsPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @SortBy NVARCHAR(50) = 'CreditDate',
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    DECLARE @SortColumn NVARCHAR(50) = CASE @SortBy
        WHEN 'CreditNumber' THEN 'CreditNumber'
        WHEN 'CreditDate' THEN 'CreditDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'CreditDate'
    END;
    DECLARE @SortDir NVARCHAR(4) = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    SELECT
        bc.[Id],
        bc.[PublicId],
        bc.[RowVersion],
        CONVERT(VARCHAR(19), bc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), bc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        bc.[VendorId],
        CONVERT(VARCHAR(19), bc.[CreditDate], 120) AS [CreditDate],
        bc.[CreditNumber],
        bc.[TotalAmount],
        bc.[Memo],
        bc.[IsDraft]
    FROM dbo.[BillCredit] bc
    LEFT JOIN dbo.[Vendor] v ON bc.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         bc.[CreditNumber] LIKE '%' + @SearchTerm + '%' OR
         bc.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), bc.[CreditDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), bc.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR bc.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR bc.[CreditDate] >= @StartDate)
        AND (@EndDate IS NULL OR bc.[CreditDate] <= @EndDate)
        AND (@IsDraft IS NULL OR bc.[IsDraft] = @IsDraft)
        AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItem] bcli
                INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = bcli.[ProjectId]
                WHERE bcli.[BillCreditId] = bc.[Id] AND up.[UserId] = @ActorUserId
            )
        )
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'CreditNumber' THEN bc.[CreditNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'CreditNumber' THEN bc.[CreditNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'CreditDate' THEN bc.[CreditDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'CreditDate' THEN bc.[CreditDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN bc.[TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN bc.[TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'VendorId' THEN bc.[VendorId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'VendorId' THEN bc.[VendorId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE CountBillCredits
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[BillCredit] bc
    LEFT JOIN dbo.[Vendor] v ON bc.[VendorId] = v.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         bc.[CreditNumber] LIKE '%' + @SearchTerm + '%' OR
         bc.[Memo] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), bc.[CreditDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), bc.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR bc.[VendorId] = @VendorId)
        AND (@StartDate IS NULL OR bc.[CreditDate] >= @StartDate)
        AND (@EndDate IS NULL OR bc.[CreditDate] <= @EndDate)
        AND (@IsDraft IS NULL OR bc.[IsDraft] = @IsDraft)
        AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItem] bcli
                INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = bcli.[ProjectId]
                WHERE bcli.[BillCreditId] = bc.[Id] AND up.[UserId] = @ActorUserId
            )
        );
    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- Expense
-- =====================================================================

CREATE OR ALTER PROCEDURE ReadExpenses
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
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
    WHERE
        @ActorIsSystemAdmin = 1
        OR @ActorUserId IS NULL
        OR EXISTS (
            SELECT 1
            FROM dbo.[ExpenseLineItem] eli
            INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = eli.[ProjectId]
            WHERE eli.[ExpenseId] = e.[Id] AND up.[UserId] = @ActorUserId
        )
    ORDER BY e.[ExpenseDate] DESC, e.[ReferenceNumber] ASC;
    COMMIT TRANSACTION;
END;
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
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    DECLARE @SortColumn NVARCHAR(50) = CASE @SortBy
        WHEN 'ReferenceNumber' THEN 'ReferenceNumber'
        WHEN 'ExpenseDate' THEN 'ExpenseDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'ExpenseDate'
    END;
    DECLARE @SortDir NVARCHAR(4) = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

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
        AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItem] eli
                INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = eli.[ProjectId]
                WHERE eli.[ExpenseId] = e.[Id] AND up.[UserId] = @ActorUserId
            )
        )
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

CREATE OR ALTER PROCEDURE CountExpenses
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @IsCredit BIT = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
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
        AND (@IsCredit IS NULL OR e.[IsCredit] = @IsCredit)
        AND (
            @ActorIsSystemAdmin = 1
            OR @ActorUserId IS NULL
            OR EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItem] eli
                INNER JOIN dbo.[UserProject] up ON up.[ProjectId] = eli.[ProjectId]
                WHERE eli.[ExpenseId] = e.[Id] AND up.[UserId] = @ActorUserId
            )
        );
    COMMIT TRANSACTION;
END;
GO

PRINT 'Gap 1 inline-EXISTS migration applied to Bill / BillCredit / Expense list paths.';
