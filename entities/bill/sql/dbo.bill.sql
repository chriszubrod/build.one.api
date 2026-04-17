IF OBJECT_ID('dbo.Bill', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Bill]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [VendorId] BIGINT NULL,
    [PaymentTermId] BIGINT NULL,
    [BillDate] DATETIME2(3) NOT NULL,
    [DueDate] DATETIME2(3) NOT NULL,
    [BillNumber] NVARCHAR(50) NULL,
    [TotalAmount] DECIMAL(18,2) NULL,
    [Memo] NVARCHAR(MAX) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_Bill_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]),
    CONSTRAINT [FK_Bill_PaymentTerm] FOREIGN KEY ([PaymentTermId]) REFERENCES [dbo].[PaymentTerm]([Id])
);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_VendorId' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_VendorId ON [dbo].[Bill] ([VendorId]);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_BillDate' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_BillDate ON [dbo].[Bill] ([BillDate]);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_BillNumber' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_BillNumber ON [dbo].[Bill] ([BillNumber]);
END
GO

IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_PaymentTermId' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE INDEX IX_Bill_PaymentTermId ON [dbo].[Bill] ([PaymentTermId]);
END
GO

-- Unique filtered index on Vendor + BillNumber + BillDate — prevents duplicates
-- Filtered to non-NULL VendorId and BillNumber so drafts without these fields are not constrained
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_VendorId_BillDate_BillNumber' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
    DROP INDEX [IX_Bill_VendorId_BillDate_BillNumber] ON [dbo].[Bill];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_Bill_VendorId_BillNumber_BillDate' AND object_id = OBJECT_ID('dbo.Bill'))
BEGIN
CREATE UNIQUE INDEX [UQ_Bill_VendorId_BillNumber_BillDate]
    ON [dbo].[Bill] ([VendorId], [BillNumber], [BillDate])
    WHERE [VendorId] IS NOT NULL AND [BillNumber] IS NOT NULL;
END
GO


-- Create Bill Stored Procedures
CREATE OR ALTER PROCEDURE CreateBill
(
    @VendorId BIGINT = NULL,
    @PaymentTermId BIGINT = NULL,
    @BillDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @BillNumber NVARCHAR(50) = NULL,
    @TotalAmount DECIMAL(18,2) = NULL,
    @Memo NVARCHAR(MAX) = NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Bill] ([CreatedDatetime], [ModifiedDatetime], [VendorId], [PaymentTermId], [BillDate], [DueDate], [BillNumber], [TotalAmount], [Memo], [IsDraft])
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
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @VendorId, @PaymentTermId, @BillDate, @DueDate, @BillNumber, @TotalAmount, @Memo, @IsDraft);

    COMMIT TRANSACTION;
END;
GO




-- Read Bills Stored Procedures
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
        [IsDraft]
    FROM dbo.[Bill]
    ORDER BY [BillDate] DESC, [BillNumber] ASC;

    COMMIT TRANSACTION;
END;
GO







-- Read Bill By Id Stored Procedures
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
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO





-- Read Bill By Public Id Stored Procedures
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
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO



-- Read Bill By Bill Number Stored Procedures
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
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber;

    COMMIT TRANSACTION;
END;
GO








-- Read Bill By Bill Number And Vendor Id Stored Procedures
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
        [IsDraft]
    FROM dbo.[Bill]
    WHERE [BillNumber] = @BillNumber
      AND [VendorId] = @VendorId
      AND (@BillDate IS NULL OR [BillDate] = @BillDate);

    COMMIT TRANSACTION;
END;
GO





-- Update Bill By Id Stored Procedures

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
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO





-- Delete Bill By Id Stored Procedures
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
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO






-- Pagination and filtering procedures
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
        b.[PaymentTermId],
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


CREATE OR ALTER PROCEDURE CountBills
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


-- Get first line item's ProjectId for a batch of bills
CREATE OR ALTER PROCEDURE ReadBillFirstLineItemProjects
(
    @BillIds NVARCHAR(MAX)  -- comma-separated bill IDs
)
AS
BEGIN
    SELECT bli.BillId, bli.ProjectId
    FROM dbo.BillLineItem bli
    INNER JOIN (
        SELECT BillId, MIN(Id) AS FirstId
        FROM dbo.BillLineItem
        WHERE BillId IN (SELECT CAST(value AS BIGINT) FROM STRING_SPLIT(@BillIds, ','))
        GROUP BY BillId
    ) first ON bli.Id = first.FirstId;
END;
GO


-- Bill completion result cache (shared across workers; TTL 1 hour)
IF OBJECT_ID('dbo.BillCompletionResult', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillCompletionResult]
(
    [BillPublicId] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
    [ResultJson] NVARCHAR(MAX) NOT NULL,
    [ExpiresAt] DATETIME2(3) NOT NULL
);
END
GO

CREATE OR ALTER PROCEDURE UpsertBillCompletionResult
(
    @BillPublicId UNIQUEIDENTIFIER,
    @ResultJson NVARCHAR(MAX),
    @ExpiresAt DATETIME2(3) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Expiry DATETIME2(3) = COALESCE(@ExpiresAt, DATEADD(HOUR, 1, SYSUTCDATETIME()));

    MERGE dbo.[BillCompletionResult] AS t
    USING (SELECT @BillPublicId AS BillPublicId) AS s ON t.[BillPublicId] = s.BillPublicId
    WHEN MATCHED THEN
        UPDATE SET [ResultJson] = @ResultJson, [ExpiresAt] = @Expiry
    WHEN NOT MATCHED THEN
        INSERT ([BillPublicId], [ResultJson], [ExpiresAt])
        VALUES (@BillPublicId, @ResultJson, @Expiry);
END;
GO

CREATE OR ALTER PROCEDURE GetBillCompletionResult
(
    @BillPublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT [ResultJson], [ExpiresAt]
    FROM dbo.[BillCompletionResult]
    WHERE [BillPublicId] = @BillPublicId AND [ExpiresAt] > SYSUTCDATETIME();
END;
GO
