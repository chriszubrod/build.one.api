DROP TABLE IF EXISTS dbo.[Bill];
GO

CREATE TABLE [dbo].[Bill]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NOT NULL,
    [TermsId] BIGINT NULL,
    [BillDate] DATETIME2(3) NOT NULL,
    [DueDate] DATETIME2(3) NOT NULL,
    [BillNumber] NVARCHAR(50) NOT NULL,
    [TotalAmount] DECIMAL(18,2) NULL,
    [Memo] NVARCHAR(MAX) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_Bill_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id])
);
GO

CREATE INDEX IX_Bill_VendorId ON [dbo].[Bill] ([VendorId]);
GO

CREATE INDEX IX_Bill_BillDate ON [dbo].[Bill] ([BillDate]);
GO

CREATE INDEX IX_Bill_BillNumber ON [dbo].[Bill] ([BillNumber]);
GO

-- Unique constraint to prevent duplicate BillNumber for the same VendorId
ALTER TABLE [dbo].[Bill]
ADD CONSTRAINT UQ_Bill_VendorId_BillNumber UNIQUE ([VendorId], [BillNumber]);
GO

DROP PROCEDURE IF EXISTS CreateBill;
GO

CREATE PROCEDURE CreateBill
(
    @VendorId BIGINT,
    @TermsId BIGINT NULL,
    @BillDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @BillNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Bill] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [TermsId], [BillDate], [DueDate], [BillNumber], [TotalAmount], [Memo], [IsDraft])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[TermsId],
        CONVERT(VARCHAR(19), INSERTED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[BillNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @VendorId, @TermsId, @BillDate, @DueDate, @BillNumber, @TotalAmount, @Memo, @IsDraft);

    COMMIT TRANSACTION;
END;

EXEC CreateBill
    @VendorId = 1,
    @TermsId = NULL,
    @BillDate = '2024-01-15',
    @DueDate = '2024-02-15',
    @BillNumber = 'BILL-001',
    @TotalAmount = 1000.00,
    @Memo = 'Sample bill';
GO

DROP PROCEDURE IF EXISTS ReadBills;
GO

CREATE PROCEDURE ReadBills
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
        [TermsId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Bill]
    ORDER BY [BillDate] DESC, [BillNumber] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadBills;
GO

DROP PROCEDURE IF EXISTS ReadBillById;
GO

CREATE PROCEDURE ReadBillById
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
        [TermsId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadBillById
    @Id = 1;
GO

DROP PROCEDURE IF EXISTS ReadBillByPublicId;
GO

CREATE PROCEDURE ReadBillByPublicId
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
        [TermsId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadBillByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

DROP PROCEDURE IF EXISTS ReadBillByBillNumber;
GO

CREATE PROCEDURE ReadBillByBillNumber
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
        [TermsId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber;

    COMMIT TRANSACTION;
END;

EXEC ReadBillByBillNumber
    @BillNumber = 'BILL-001';
GO

DROP PROCEDURE IF EXISTS ReadBillByBillNumberAndVendorId;
GO

CREATE PROCEDURE ReadBillByBillNumberAndVendorId
(
    @BillNumber NVARCHAR(50),
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
        [TermsId],
        CONVERT(VARCHAR(19), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber AND [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;

EXEC ReadBillByBillNumberAndVendorId
    @BillNumber = 'BILL-001',
    @VendorId = 1;
GO

DROP PROCEDURE IF EXISTS UpdateBillById;
GO

CREATE PROCEDURE UpdateBillById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @TermsId BIGINT NULL,
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

    UPDATE dbo.[Bill]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [TermsId] = @TermsId,
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
        INSERTED.[TermsId],
        CONVERT(VARCHAR(19), INSERTED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[BillNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;

EXEC UpdateBillById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @VendorId = 1,
    @TermsId = NULL,
    @BillDate = '2024-01-20',
    @DueDate = '2024-02-20',
    @BillNumber = 'BILL-002',
    @TotalAmount = 1500.00,
    @Memo = 'Updated bill';
GO

DROP PROCEDURE IF EXISTS DeleteBillById;
GO

CREATE PROCEDURE DeleteBillById
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
        DELETED.[TermsId],
        CONVERT(VARCHAR(19), DELETED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), DELETED.[DueDate], 120) AS [DueDate],
        DELETED.[BillNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteBillById
    @Id = 3;
GO

SELECT * FROM dbo.Bill;

ALTER TABLE [dbo].[Bill] DROP COLUMN [LineItemId];
GO

-- Pagination and filtering procedures
DROP PROCEDURE IF EXISTS ReadBillsPaginated;
GO

CREATE PROCEDURE ReadBillsPaginated
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
    
    -- Validate sort column to prevent SQL injection
    DECLARE @SortColumn NVARCHAR(50);
    SET @SortColumn = CASE @SortBy
        WHEN 'BillNumber' THEN 'BillNumber'
        WHEN 'BillDate' THEN 'BillDate'
        WHEN 'DueDate' THEN 'DueDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'BillDate'
    END;
    
    -- Validate sort direction
    DECLARE @SortDir NVARCHAR(4);
    SET @SortDir = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;
    
    SELECT
        b.[Id],
        b.[PublicId],
        b.[RowVersion],
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), b.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        b.[VendorId],
        b.[TermsId],
        CONVERT(VARCHAR(19), b.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), b.[DueDate], 120) AS [DueDate],
        b.[BillNumber],
        b.[TotalAmount],
        b.[Memo],
        b.[IsDraft]
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

DROP PROCEDURE IF EXISTS CountBills;
GO

CREATE PROCEDURE CountBills
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @StartDate DATETIME2(3) = NULL,
    @EndDate DATETIME2(3) = NULL,
    @IsDraft BIT = NULL
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
        AND (@IsDraft IS NULL OR b.[IsDraft] = @IsDraft);
    
    COMMIT TRANSACTION;
END;
GO

SELECT COUNT(Bill.Id) AS BillCount
FROM dbo.Bill;

SELECT
    CONVERT(VARCHAR(7), Bill.BillDate, 120) AS BillMonth,
    COUNT(Bill.Id) AS BillCount
FROM dbo.Bill
GROUP BY
    CONVERT(VARCHAR(7), Bill.BillDate, 120)
ORDER BY
    BillMonth;


