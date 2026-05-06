-- Gap 1 — List-path read sprocs scoped by UserProject membership.
--
-- Per Q1.1 + Q1.2 + Q1.3: enforces UserProject scoping on the list /
-- paginated / count read sprocs across the 5 transactional entities
-- whose direct lookups (by_id / by_public_id / by_other_keys) are NOT
-- yet scoped — those land in a follow-up tightening pass.
--
-- Project's full read surface (4 sprocs) is already scoped via
-- entities/project/sql/migrations/001_gap1_scope_by_user_project.sql.
--
-- Filter: each affected sproc gains
--     @ActorUserId BIGINT = NULL,
--     @ActorIsSystemAdmin BIT = NULL
-- and an `AND dbo.UserCanAccess<Entity>(...) = 1` clause. NULL
-- @ActorUserId bypasses (back-compat during deploy).
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- =====================================================================
-- Bill — line items carry ProjectId
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
    WHERE dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) = 1
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
        AND dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) = 1
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
        AND dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) = 1;
    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- BillCredit — line items carry ProjectId
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
    WHERE dbo.UserCanAccessBillCredit(@ActorUserId, @ActorIsSystemAdmin, bc.[Id]) = 1
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
        AND dbo.UserCanAccessBillCredit(@ActorUserId, @ActorIsSystemAdmin, bc.[Id]) = 1
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
        AND dbo.UserCanAccessBillCredit(@ActorUserId, @ActorIsSystemAdmin, bc.[Id]) = 1;
    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- Expense — line items carry ProjectId
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
    WHERE dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1
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
        AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1
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
        AND dbo.UserCanAccessExpense(@ActorUserId, @ActorIsSystemAdmin, e.[Id]) = 1;
    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- Invoice — direct ProjectId on parent
-- =====================================================================

CREATE OR ALTER PROCEDURE ReadInvoices
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        i.[Id],
        i.[PublicId],
        i.[RowVersion],
        CONVERT(VARCHAR(19), i.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), i.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        i.[ProjectId],
        i.[PaymentTermId],
        CONVERT(VARCHAR(19), i.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), i.[DueDate], 120) AS [DueDate],
        i.[InvoiceNumber],
        i.[TotalAmount],
        i.[Memo],
        i.[IsDraft]
    FROM dbo.[Invoice] i
    WHERE dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, i.[ProjectId]) = 1
    ORDER BY i.[InvoiceDate] DESC, i.[InvoiceNumber] ASC;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadInvoicesPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @ProjectId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL,
    @SortBy NVARCHAR(50) = 'InvoiceDate',
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    DECLARE @SortColumn NVARCHAR(50) = CASE @SortBy
        WHEN 'InvoiceNumber' THEN 'InvoiceNumber'
        WHEN 'InvoiceDate' THEN 'InvoiceDate'
        WHEN 'DueDate' THEN 'DueDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'ProjectId' THEN 'ProjectId'
        ELSE 'InvoiceDate'
    END;
    DECLARE @SortDir NVARCHAR(4) = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

    SELECT
        i.[Id],
        i.[PublicId],
        i.[RowVersion],
        CONVERT(VARCHAR(19), i.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), i.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        i.[ProjectId],
        i.[PaymentTermId],
        CONVERT(VARCHAR(19), i.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), i.[DueDate], 120) AS [DueDate],
        i.[InvoiceNumber],
        i.[TotalAmount],
        i.[Memo],
        i.[IsDraft]
    FROM dbo.[Invoice] i
    LEFT JOIN dbo.[Project] p ON i.[ProjectId] = p.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         i.[InvoiceNumber] LIKE '%' + @SearchTerm + '%' OR
         i.[Memo] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[InvoiceDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[DueDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), i.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@ProjectId IS NULL OR i.[ProjectId] = @ProjectId)
        AND (@StartDate IS NULL OR i.[InvoiceDate] >= @StartDate)
        AND (@EndDate IS NULL OR i.[InvoiceDate] <= @EndDate)
        AND (@IsDraft IS NULL OR i.[IsDraft] = @IsDraft)
        AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, i.[ProjectId]) = 1
    ORDER BY
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'InvoiceNumber' THEN i.[InvoiceNumber] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'InvoiceNumber' THEN i.[InvoiceNumber] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'InvoiceDate' THEN i.[InvoiceDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'InvoiceDate' THEN i.[InvoiceDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'DueDate' THEN i.[DueDate] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'DueDate' THEN i.[DueDate] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'TotalAmount' THEN i.[TotalAmount] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'TotalAmount' THEN i.[TotalAmount] END DESC,
        CASE WHEN @SortDir = 'ASC' AND @SortColumn = 'ProjectId' THEN i.[ProjectId] END ASC,
        CASE WHEN @SortDir = 'DESC' AND @SortColumn = 'ProjectId' THEN i.[ProjectId] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE CountInvoices
(
    @SearchTerm NVARCHAR(255) = NULL,
    @ProjectId BIGINT = NULL,
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
    FROM dbo.[Invoice] i
    LEFT JOIN dbo.[Project] p ON i.[ProjectId] = p.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         i.[InvoiceNumber] LIKE '%' + @SearchTerm + '%' OR
         i.[Memo] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[InvoiceDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(10), i.[DueDate], 120) LIKE '%' + @SearchTerm + '%' OR
         CONVERT(VARCHAR(50), i.[TotalAmount]) LIKE '%' + @SearchTerm + '%')
        AND (@ProjectId IS NULL OR i.[ProjectId] = @ProjectId)
        AND (@StartDate IS NULL OR i.[InvoiceDate] >= @StartDate)
        AND (@EndDate IS NULL OR i.[InvoiceDate] <= @EndDate)
        AND (@IsDraft IS NULL OR i.[IsDraft] = @IsDraft)
        AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, i.[ProjectId]) = 1;
    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- ContractLabor — direct ProjectId on parent
-- =====================================================================

CREATE OR ALTER PROCEDURE ReadContractLabors
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        cl.[Id],
        cl.[PublicId],
        cl.[RowVersion],
        CONVERT(VARCHAR(19), cl.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cl.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        cl.[VendorId],
        cl.[ProjectId],
        cl.[EmployeeName],
        cl.[JobName],
        CONVERT(VARCHAR(10), cl.[WorkDate], 120) AS [WorkDate],
        cl.[TimeIn],
        cl.[TimeOut],
        cl.[BreakTime],
        cl.[RegularHours],
        cl.[OvertimeHours],
        cl.[TotalHours],
        cl.[HourlyRate],
        cl.[Markup],
        cl.[TotalAmount],
        cl.[SubCostCodeId],
        cl.[Description],
        CONVERT(VARCHAR(10), cl.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        cl.[Status],
        cl.[BillLineItemId],
        cl.[BillVendorId],
        CONVERT(VARCHAR(10), cl.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(10), cl.[DueDate], 120) AS [DueDate],
        cl.[BillNumber],
        cl.[ImportBatchId],
        cl.[SourceFile],
        cl.[SourceRow]
    FROM dbo.[ContractLabor] cl
    WHERE dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, cl.[ProjectId]) = 1
    ORDER BY cl.[WorkDate] DESC, cl.[EmployeeName] ASC, cl.[JobName] ASC;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractLaborsPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @BillingPeriodStart DATE = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL,
    @SortBy NVARCHAR(50) = 'WorkDate',
    @SortDirection NVARCHAR(4) = 'DESC',
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;

    SELECT
        cl.[Id],
        cl.[PublicId],
        cl.[RowVersion],
        CONVERT(VARCHAR(19), cl.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cl.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        cl.[VendorId],
        cl.[ProjectId],
        cl.[EmployeeName],
        cl.[JobName],
        CONVERT(VARCHAR(10), cl.[WorkDate], 120) AS [WorkDate],
        cl.[TimeIn],
        cl.[TimeOut],
        cl.[BreakTime],
        cl.[RegularHours],
        cl.[OvertimeHours],
        cl.[TotalHours],
        cl.[HourlyRate],
        cl.[Markup],
        cl.[TotalAmount],
        cl.[SubCostCodeId],
        cl.[Description],
        CONVERT(VARCHAR(10), cl.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        cl.[Status],
        cl.[BillLineItemId],
        cl.[BillVendorId],
        CONVERT(VARCHAR(10), cl.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(10), cl.[DueDate], 120) AS [DueDate],
        cl.[BillNumber],
        cl.[ImportBatchId],
        cl.[SourceFile],
        cl.[SourceRow]
    FROM dbo.[ContractLabor] cl
    LEFT JOIN dbo.[Vendor] v ON cl.[VendorId] = v.[Id]
    LEFT JOIN dbo.[Project] p ON cl.[ProjectId] = p.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         cl.[EmployeeName] LIKE '%' + @SearchTerm + '%' OR
         cl.[JobName] LIKE '%' + @SearchTerm + '%' OR
         cl.[Description] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR cl.[VendorId] = @VendorId OR cl.[BillVendorId] = @VendorId)
        AND (@ProjectId IS NULL OR cl.[ProjectId] = @ProjectId)
        AND (@Status IS NULL OR cl.[Status] = @Status)
        AND (@BillingPeriodStart IS NULL OR cl.[BillingPeriodStart] = @BillingPeriodStart)
        AND (@StartDate IS NULL OR cl.[WorkDate] >= @StartDate)
        AND (@EndDate IS NULL OR cl.[WorkDate] <= @EndDate)
        AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, cl.[ProjectId]) = 1
    ORDER BY
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'WorkDate' THEN cl.[WorkDate] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'WorkDate' THEN cl.[WorkDate] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'EmployeeName' THEN cl.[EmployeeName] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'EmployeeName' THEN cl.[EmployeeName] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'TotalHours' THEN cl.[TotalHours] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'TotalHours' THEN cl.[TotalHours] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'TotalAmount' THEN cl.[TotalAmount] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'TotalAmount' THEN cl.[TotalAmount] END DESC,
        ISNULL(v.[Name], cl.[EmployeeName]) ASC,
        cl.[JobName] ASC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;
    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE CountContractLabors
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @BillingPeriodStart DATE = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[ContractLabor] cl
    LEFT JOIN dbo.[Vendor] v ON cl.[VendorId] = v.[Id]
    LEFT JOIN dbo.[Project] p ON cl.[ProjectId] = p.[Id]
    WHERE
        (@SearchTerm IS NULL OR
         cl.[EmployeeName] LIKE '%' + @SearchTerm + '%' OR
         cl.[JobName] LIKE '%' + @SearchTerm + '%' OR
         cl.[Description] LIKE '%' + @SearchTerm + '%' OR
         v.[Name] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%')
        AND (@VendorId IS NULL OR cl.[VendorId] = @VendorId OR cl.[BillVendorId] = @VendorId)
        AND (@ProjectId IS NULL OR cl.[ProjectId] = @ProjectId)
        AND (@Status IS NULL OR cl.[Status] = @Status)
        AND (@BillingPeriodStart IS NULL OR cl.[BillingPeriodStart] = @BillingPeriodStart)
        AND (@StartDate IS NULL OR cl.[WorkDate] >= @StartDate)
        AND (@EndDate IS NULL OR cl.[WorkDate] <= @EndDate)
        AND dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, cl.[ProjectId]) = 1;
    COMMIT TRANSACTION;
END;
GO

PRINT 'Gap 1 list-path scoping applied to Bill / BillCredit / Expense / Invoice / ContractLabor.';
