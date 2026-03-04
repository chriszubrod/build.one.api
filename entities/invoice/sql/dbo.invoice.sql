IF OBJECT_ID('dbo.Invoice', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Invoice]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NOT NULL,
    [PaymentTermId] BIGINT NULL,
    [InvoiceDate] DATETIME2(3) NOT NULL,
    [DueDate] DATETIME2(3) NOT NULL,
    [InvoiceNumber] NVARCHAR(50) NOT NULL,
    [TotalAmount] DECIMAL(18,2) NULL,
    [Memo] NVARCHAR(MAX) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_Invoice_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [FK_Invoice_PaymentTerm] FOREIGN KEY ([PaymentTermId]) REFERENCES [dbo].[PaymentTerm]([Id])
);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_ProjectId' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_ProjectId ON [dbo].[Invoice] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_InvoiceDate' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_InvoiceDate ON [dbo].[Invoice] ([InvoiceDate]);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_InvoiceNumber' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_InvoiceNumber ON [dbo].[Invoice] ([InvoiceNumber]);
END
GO

IF OBJECT_ID('dbo.Invoice', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Invoice_PaymentTermId' AND object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
CREATE INDEX IX_Invoice_PaymentTermId ON [dbo].[Invoice] ([PaymentTermId]);
END
GO

-- Unique constraint to prevent duplicate InvoiceNumber for the same ProjectId
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = 'UQ_Invoice_ProjectId_InvoiceNumber' AND parent_object_id = OBJECT_ID('dbo.Invoice'))
BEGIN
ALTER TABLE [dbo].[Invoice]
ADD CONSTRAINT UQ_Invoice_ProjectId_InvoiceNumber UNIQUE ([ProjectId], [InvoiceNumber]);
END
GO


CREATE OR ALTER PROCEDURE CreateInvoice
(
    @ProjectId BIGINT,
    @PaymentTermId BIGINT NULL,
    @InvoiceDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @InvoiceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Invoice] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [PaymentTermId], [InvoiceDate], [DueDate], [InvoiceNumber], [TotalAmount], [Memo], [IsDraft])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[InvoiceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @ProjectId, @PaymentTermId, @InvoiceDate, @DueDate, @InvoiceNumber, @TotalAmount, @Memo, @IsDraft);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoices
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    ORDER BY [InvoiceDate] DESC, [InvoiceNumber] ASC;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceById
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceByPublicId
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceByInvoiceNumber
(
    @InvoiceNumber NVARCHAR(50)
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [InvoiceNumber] = @InvoiceNumber;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadInvoiceByInvoiceNumberAndProjectId
(
    @InvoiceNumber NVARCHAR(50),
    @ProjectId BIGINT
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
        [ProjectId],
        [PaymentTermId],
        CONVERT(VARCHAR(19), [InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), [DueDate], 120) AS [DueDate],
        [InvoiceNumber],
        [TotalAmount],
        [Memo],
        [IsDraft]
    FROM dbo.[Invoice]
    WHERE [InvoiceNumber] = @InvoiceNumber AND [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateInvoiceById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ProjectId BIGINT,
    @PaymentTermId BIGINT NULL,
    @InvoiceDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @InvoiceNumber NVARCHAR(50),
    @TotalAmount DECIMAL(18,2) NULL,
    @Memo NVARCHAR(MAX) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Invoice]
    SET
        [ModifiedDatetime] = @Now,
        [ProjectId] = @ProjectId,
        [PaymentTermId] = @PaymentTermId,
        [InvoiceDate] = @InvoiceDate,
        [DueDate] = @DueDate,
        [InvoiceNumber] = @InvoiceNumber,
        [TotalAmount] = @TotalAmount,
        [Memo] = @Memo,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[InvoiceNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteInvoiceById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Invoice]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[PaymentTermId],
        CONVERT(VARCHAR(19), DELETED.[InvoiceDate], 120) AS [InvoiceDate],
        CONVERT(VARCHAR(19), DELETED.[DueDate], 120) AS [DueDate],
        DELETED.[InvoiceNumber],
        DELETED.[TotalAmount],
        DELETED.[Memo],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

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
    @SortDirection NVARCHAR(4) = 'DESC'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;

    DECLARE @SortColumn NVARCHAR(50);
    SET @SortColumn = CASE @SortBy
        WHEN 'InvoiceNumber' THEN 'InvoiceNumber'
        WHEN 'InvoiceDate' THEN 'InvoiceDate'
        WHEN 'DueDate' THEN 'DueDate'
        WHEN 'TotalAmount' THEN 'TotalAmount'
        WHEN 'ProjectId' THEN 'ProjectId'
        ELSE 'InvoiceDate'
    END;

    DECLARE @SortDir NVARCHAR(4);
    SET @SortDir = CASE WHEN UPPER(@SortDirection) = 'ASC' THEN 'ASC' ELSE 'DESC' END;

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
    @IsDraft BIT = NULL
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
        AND (@IsDraft IS NULL OR i.[IsDraft] = @IsDraft);

    COMMIT TRANSACTION;
END;
GO
