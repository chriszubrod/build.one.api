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

-- SUPERSEDED (U-089): dbo.ReadBills single-sourced in entities/bill/sql/dbo.bill.sql.
GO

-- SUPERSEDED (U-089): dbo.ReadBillsPaginated single-sourced in entities/bill/sql/dbo.bill.sql.
GO

-- SUPERSEDED (U-089): dbo.CountBills single-sourced in entities/bill/sql/dbo.bill.sql.
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

-- SUPERSEDED (U-089): dbo.ReadExpenses single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-089): dbo.ReadExpensesPaginated single-sourced in entities/expense/sql/dbo.expense.sql.
GO

-- SUPERSEDED (U-089): dbo.CountExpenses single-sourced in entities/expense/sql/dbo.expense.sql.
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

-- SUPERSEDED (U-089): dbo.ReadContractLabors single-sourced in entities/contract_labor/sql/dbo.contract_labor.sql.
GO

-- SUPERSEDED (U-089): dbo.ReadContractLaborsPaginated single-sourced in entities/contract_labor/sql/dbo.contract_labor.sql.
GO

-- SUPERSEDED (U-089): dbo.CountContractLabors single-sourced in entities/contract_labor/sql/dbo.contract_labor.sql.
GO

PRINT 'Gap 1 list-path scoping applied to Bill / BillCredit / Expense / Invoice / ContractLabor.';
