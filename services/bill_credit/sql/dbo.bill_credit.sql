GO

IF OBJECT_ID('dbo.BillCredit', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillCredit]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NOT NULL,
    [CreditDate] DATETIME2(3) NOT NULL,
    [CreditNumber] NVARCHAR(50) NOT NULL,
    [TotalAmount] DECIMAL(18,2) NULL,
    [Memo] NVARCHAR(MAX) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_BillCredit_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id])
);
END
GO

IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillCredit_VendorId' AND object_id = OBJECT_ID('dbo.BillCredit'))
BEGIN
CREATE INDEX IX_BillCredit_VendorId ON [dbo].[BillCredit] ([VendorId]);
END
GO

IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillCredit_CreditDate' AND object_id = OBJECT_ID('dbo.BillCredit'))
BEGIN
CREATE INDEX IX_BillCredit_CreditDate ON [dbo].[BillCredit] ([CreditDate]);
END
GO

IF OBJECT_ID('dbo.BillCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillCredit_CreditNumber' AND object_id = OBJECT_ID('dbo.BillCredit'))
BEGIN
CREATE INDEX IX_BillCredit_CreditNumber ON [dbo].[BillCredit] ([CreditNumber]);
END
GO

-- Unique constraint to prevent duplicate CreditNumber for the same VendorId
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_BillCredit_VendorId_CreditNumber' AND parent_object_id = OBJECT_ID('dbo.BillCredit'))
BEGIN
ALTER TABLE [dbo].[BillCredit]
ADD CONSTRAINT UQ_BillCredit_VendorId_CreditNumber UNIQUE ([VendorId], [CreditNumber]);
END
GO

GO

CREATE OR ALTER PROCEDURE CreateBillCredit
(
    @VendorId BIGINT,
    @CreditDate DATETIME2(3),
    @CreditNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[BillCredit] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [CreditDate], [CreditNumber], [TotalAmount], [Memo], [IsDraft])
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
    VALUES (@Now, @Now, @VendorId, @CreditDate, @CreditNumber, @TotalAmount, @Memo, @IsDraft);

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCredits
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
        CONVERT(VARCHAR(19), [CreditDate], 120) AS [CreditDate],
        [CreditNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[BillCredit]
    ORDER BY [CreditDate] DESC, [CreditNumber] ASC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCreditById
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
        CONVERT(VARCHAR(19), [CreditDate], 120) AS [CreditDate],
        [CreditNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[BillCredit]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCreditByPublicId
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
        CONVERT(VARCHAR(19), [CreditDate], 120) AS [CreditDate],
        [CreditNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[BillCredit]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadBillCreditByCreditNumberAndVendorId
(
    @CreditNumber NVARCHAR(50),
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
        CONVERT(VARCHAR(19), [CreditDate], 120) AS [CreditDate],
        [CreditNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[BillCredit]
    WHERE [CreditNumber] = @CreditNumber AND [VendorId] = @VendorId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateBillCreditById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT,
    @CreditDate DATETIME2(3),
    @CreditNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[BillCredit]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [CreditDate] = @CreditDate,
        [CreditNumber] = @CreditNumber,
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
        CONVERT(VARCHAR(19), INSERTED.[CreditDate], 120) AS [CreditDate],
        INSERTED.[CreditNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE DeleteBillCreditById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[BillCredit]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        CONVERT(VARCHAR(19), DELETED.[CreditDate], 120) AS [CreditDate],
        DELETED.[CreditNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- Pagination and filtering procedures
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
    @SortDirection NVARCHAR(4) = 'DESC'
)
AS
BEGIN
    BEGIN TRANSACTION;
    
    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;
    
    -- Validate sort column to prevent SQL injection
    DECLARE @SortColumn NVARCHAR(50);
    SET @SortColumn = CASE @SortBy
        WHEN 'CreditNumber' THEN 'CreditNumber'
        WHEN 'CreditDate' THEN 'CreditDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'VendorId' THEN 'VendorId'
        ELSE 'CreditDate'
    END;
    
    -- Validate sort direction
    DECLARE @SortDir NVARCHAR(4);
    SET @SortDir = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;
    
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

GO

CREATE OR ALTER PROCEDURE CountBillCredits
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
        AND (@IsDraft IS NULL OR bc.[IsDraft] = @IsDraft);
    
    COMMIT TRANSACTION;
END;
GO
