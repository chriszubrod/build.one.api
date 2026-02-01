-- ContractLabor Table
-- Stores time log entries imported from Excel for contract labor billing

GO

IF OBJECT_ID('dbo.ContractLabor', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ContractLabor]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    
    -- Source data from Excel
    [VendorId] BIGINT NULL,                        -- Matched from Name column (assigned during review)
    [ProjectId] BIGINT NULL,                       -- Matched from Job column (assigned during review)
    [EmployeeName] NVARCHAR(255) NOT NULL,         -- Original Name value from Excel
    [JobName] NVARCHAR(255) NULL,                  -- Original Job value from Excel
    [WorkDate] DATE NOT NULL,                      -- Date column
    
    -- Time data
    [TimeIn] NVARCHAR(20) NULL,                    -- Time In column
    [TimeOut] NVARCHAR(20) NULL,                   -- Time Out column
    [BreakTime] NVARCHAR(20) NULL,                 -- Break Time column
    [RegularHours] DECIMAL(6,2) NULL,              -- Regular Time (parsed)
    [OvertimeHours] DECIMAL(6,2) NULL,             -- OT column (parsed)
    [TotalHours] DECIMAL(6,2) NOT NULL,            -- Total Work Time (parsed)
    
    -- Rates & amounts (entered during review)
    [HourlyRate] DECIMAL(18,4) NULL,               -- Rate per hour
    [Markup] DECIMAL(18,4) NULL,                   -- Markup percentage
    [TotalAmount] DECIMAL(18,2) NULL,              -- Calculated: hours * rate * (1 + markup)
    
    -- Assignment (manual entry)
    [SubCostCodeId] BIGINT NULL,                   -- Assigned during review
    [Description] NVARCHAR(MAX) NULL,              -- Notes column + editable
    
    -- Billing period
    [BillingPeriodStart] DATE NULL,                -- Start of billing period (1st or 16th)
    [Status] NVARCHAR(20) NOT NULL DEFAULT 'pending_review',  -- pending_review, ready, billed
    [BillLineItemId] BIGINT NULL,                  -- Legacy: Set when billed via old process
    
    -- Bill header fields (for PDF generation)
    [BillVendorId] BIGINT NULL,                    -- Selected vendor for billing (may differ from import)
    [BillDate] DATE NULL,                          -- Bill date
    [DueDate] DATE NULL,                           -- Due date
    [BillNumber] NVARCHAR(50) NULL,                -- Bill number
    
    -- Source tracking
    [ImportBatchId] NVARCHAR(50) NULL,             -- Groups imports together
    [SourceFile] NVARCHAR(255) NULL,               -- Original filename
    [SourceRow] INT NULL,                          -- Row number in Excel
    
    CONSTRAINT [FK_ContractLabor_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]),
    CONSTRAINT [FK_ContractLabor_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [FK_ContractLabor_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [FK_ContractLabor_BillLineItem] FOREIGN KEY ([BillLineItemId]) REFERENCES [dbo].[BillLineItem]([Id]),
    CONSTRAINT [FK_ContractLabor_BillVendor] FOREIGN KEY ([BillVendorId]) REFERENCES [dbo].[Vendor]([Id])
);
END
GO


-- ContractLaborLineItem Table
-- Stores line items for contract labor bills (many per ContractLabor entry)
GO

IF OBJECT_ID('dbo.ContractLaborLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ContractLaborLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    
    -- Parent reference
    [ContractLaborId] BIGINT NOT NULL,             -- FK to ContractLabor
    
    -- Line item details
    [LineDate] DATE NULL,                          -- Date for this line (defaults to parent WorkDate)
    [ProjectId] BIGINT NULL,                       -- FK to Project
    [SubCostCodeId] BIGINT NULL,                   -- FK to SubCostCode
    [Description] NVARCHAR(MAX) NULL,              -- Line item description
    [Hours] DECIMAL(6,2) NULL,                     -- Hours allocated to this line
    [Rate] DECIMAL(18,4) NULL,                     -- Hourly rate
    [Markup] DECIMAL(18,4) NULL,                   -- Markup percentage (e.g., 0.05 for 5%)
    [Price] DECIMAL(18,2) NULL,                    -- Calculated: (Hours / 8 * Rate) * (1 + Markup)
    [IsBillable] BIT NOT NULL DEFAULT 1,           -- Whether this line is billable
    
    CONSTRAINT [FK_ContractLaborLineItem_ContractLabor] FOREIGN KEY ([ContractLaborId]) REFERENCES [dbo].[ContractLabor]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_ContractLaborLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [FK_ContractLaborLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id])
);
END
GO

IF OBJECT_ID('dbo.ContractLaborLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLaborLineItem_ContractLaborId' AND object_id = OBJECT_ID('dbo.ContractLaborLineItem'))
BEGIN
CREATE INDEX IX_ContractLaborLineItem_ContractLaborId ON [dbo].[ContractLaborLineItem] ([ContractLaborId]);
END
GO

-- Indexes for common queries
IF OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_VendorId' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
CREATE INDEX IX_ContractLabor_VendorId ON [dbo].[ContractLabor] ([VendorId]);
END
GO

IF OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_ProjectId' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
CREATE INDEX IX_ContractLabor_ProjectId ON [dbo].[ContractLabor] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_WorkDate' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
CREATE INDEX IX_ContractLabor_WorkDate ON [dbo].[ContractLabor] ([WorkDate]);
END
GO

IF OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_Status' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
CREATE INDEX IX_ContractLabor_Status ON [dbo].[ContractLabor] ([Status]);
END
GO

IF OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_BillingPeriodStart' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
CREATE INDEX IX_ContractLabor_BillingPeriodStart ON [dbo].[ContractLabor] ([BillingPeriodStart]);
END
GO

IF OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_ImportBatchId' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
CREATE INDEX IX_ContractLabor_ImportBatchId ON [dbo].[ContractLabor] ([ImportBatchId]);
END
GO

IF OBJECT_ID('dbo.ContractLabor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ContractLabor_PublicId' AND object_id = OBJECT_ID('dbo.ContractLabor'))
BEGIN
CREATE INDEX IX_ContractLabor_PublicId ON [dbo].[ContractLabor] ([PublicId]);
END
GO


-- Stored Procedures

GO

CREATE OR ALTER PROCEDURE CreateContractLabor
(
    @VendorId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @EmployeeName NVARCHAR(255),
    @JobName NVARCHAR(255) NULL,
    @WorkDate DATE,
    @TimeIn NVARCHAR(20) NULL,
    @TimeOut NVARCHAR(20) NULL,
    @BreakTime NVARCHAR(20) NULL,
    @RegularHours DECIMAL(6,2) NULL,
    @OvertimeHours DECIMAL(6,2) NULL,
    @TotalHours DECIMAL(6,2),
    @HourlyRate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @TotalAmount DECIMAL(18,2) NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @BillingPeriodStart DATE NULL,
    @Status NVARCHAR(20) = 'pending_review',
    @BillLineItemId BIGINT NULL,
    @ImportBatchId NVARCHAR(50) NULL,
    @SourceFile NVARCHAR(255) NULL,
    @SourceRow INT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ContractLabor] (
        [CreatedDatetime], [ModifiedDatetime], [VendorId], [ProjectId], [EmployeeName], [JobName],
        [WorkDate], [TimeIn], [TimeOut], [BreakTime], [RegularHours], [OvertimeHours],
        [TotalHours], [HourlyRate], [Markup], [TotalAmount], [SubCostCodeId], [Description],
        [BillingPeriodStart], [Status], [BillLineItemId], [ImportBatchId], [SourceFile], [SourceRow]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[EmployeeName],
        INSERTED.[JobName],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[TimeIn],
        INSERTED.[TimeOut],
        INSERTED.[BreakTime],
        INSERTED.[RegularHours],
        INSERTED.[OvertimeHours],
        INSERTED.[TotalHours],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[TotalAmount],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        INSERTED.[Status],
        INSERTED.[BillLineItemId],
        INSERTED.[ImportBatchId],
        INSERTED.[SourceFile],
        INSERTED.[SourceRow]
    VALUES (
        @Now, @Now, @VendorId, @ProjectId, @EmployeeName, @JobName,
        @WorkDate, @TimeIn, @TimeOut, @BreakTime, @RegularHours, @OvertimeHours,
        @TotalHours, @HourlyRate, @Markup, @TotalAmount, @SubCostCodeId, @Description,
        @BillingPeriodStart, @Status, @BillLineItemId, @ImportBatchId, @SourceFile, @SourceRow
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLabors
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    ORDER BY [WorkDate] DESC, [EmployeeName] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborById
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborByPublicId
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborsByVendorId
(
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    WHERE [VendorId] = @VendorId
    ORDER BY [WorkDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborsByBillingPeriod
(
    @BillingPeriodStart DATE
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    WHERE [BillingPeriodStart] = @BillingPeriodStart
    ORDER BY [VendorId], [WorkDate];

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborsByStatus
(
    @Status NVARCHAR(20)
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    WHERE [Status] = @Status
    ORDER BY [BillingPeriodStart], [VendorId], [WorkDate];

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborsByImportBatchId
(
    @ImportBatchId NVARCHAR(50)
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    WHERE [ImportBatchId] = @ImportBatchId
    ORDER BY [SourceRow];

    COMMIT TRANSACTION;
END;
GO


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
    @SortDirection NVARCHAR(4) = 'DESC'
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
        AND (@VendorId IS NULL OR cl.[VendorId] = @VendorId)
        AND (@ProjectId IS NULL OR cl.[ProjectId] = @ProjectId)
        AND (@Status IS NULL OR cl.[Status] = @Status)
        AND (@BillingPeriodStart IS NULL OR cl.[BillingPeriodStart] = @BillingPeriodStart)
        AND (@StartDate IS NULL OR cl.[WorkDate] >= @StartDate)
        AND (@EndDate IS NULL OR cl.[WorkDate] <= @EndDate)
    ORDER BY 
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'WorkDate' THEN cl.[WorkDate] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'WorkDate' THEN cl.[WorkDate] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'EmployeeName' THEN cl.[EmployeeName] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'EmployeeName' THEN cl.[EmployeeName] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'TotalHours' THEN cl.[TotalHours] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'TotalHours' THEN cl.[TotalHours] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'TotalAmount' THEN cl.[TotalAmount] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'TotalAmount' THEN cl.[TotalAmount] END DESC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;
    
    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE CountContractLabors
(
    @SearchTerm NVARCHAR(255) = NULL,
    @VendorId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @BillingPeriodStart DATE = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL
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
        AND (@VendorId IS NULL OR cl.[VendorId] = @VendorId)
        AND (@ProjectId IS NULL OR cl.[ProjectId] = @ProjectId)
        AND (@Status IS NULL OR cl.[Status] = @Status)
        AND (@BillingPeriodStart IS NULL OR cl.[BillingPeriodStart] = @BillingPeriodStart)
        AND (@StartDate IS NULL OR cl.[WorkDate] >= @StartDate)
        AND (@EndDate IS NULL OR cl.[WorkDate] <= @EndDate);
    
    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateContractLaborById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @VendorId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @EmployeeName NVARCHAR(255),
    @JobName NVARCHAR(255) NULL,
    @WorkDate DATE,
    @TimeIn NVARCHAR(20) NULL,
    @TimeOut NVARCHAR(20) NULL,
    @BreakTime NVARCHAR(20) NULL,
    @RegularHours DECIMAL(6,2) NULL,
    @OvertimeHours DECIMAL(6,2) NULL,
    @TotalHours DECIMAL(6,2),
    @HourlyRate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @TotalAmount DECIMAL(18,2) NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @BillingPeriodStart DATE NULL,
    @Status NVARCHAR(20) = NULL,
    @BillLineItemId BIGINT NULL,
    @ImportBatchId NVARCHAR(50) NULL,
    @SourceFile NVARCHAR(255) NULL,
    @SourceRow INT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ContractLabor]
    SET
        [ModifiedDatetime] = @Now,
        [VendorId] = @VendorId,
        [ProjectId] = @ProjectId,
        [EmployeeName] = @EmployeeName,
        [JobName] = @JobName,
        [WorkDate] = @WorkDate,
        [TimeIn] = @TimeIn,
        [TimeOut] = @TimeOut,
        [BreakTime] = @BreakTime,
        [RegularHours] = @RegularHours,
        [OvertimeHours] = @OvertimeHours,
        [TotalHours] = @TotalHours,
        [HourlyRate] = @HourlyRate,
        [Markup] = @Markup,
        [TotalAmount] = @TotalAmount,
        [SubCostCodeId] = @SubCostCodeId,
        [Description] = @Description,
        [BillingPeriodStart] = @BillingPeriodStart,
        [Status] = CASE WHEN @Status IS NULL THEN [Status] ELSE @Status END,
        [BillLineItemId] = @BillLineItemId,
        [ImportBatchId] = @ImportBatchId,
        [SourceFile] = @SourceFile,
        [SourceRow] = @SourceRow
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[EmployeeName],
        INSERTED.[JobName],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[TimeIn],
        INSERTED.[TimeOut],
        INSERTED.[BreakTime],
        INSERTED.[RegularHours],
        INSERTED.[OvertimeHours],
        INSERTED.[TotalHours],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[TotalAmount],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        INSERTED.[Status],
        INSERTED.[BillLineItemId],
        INSERTED.[ImportBatchId],
        INSERTED.[SourceFile],
        INSERTED.[SourceRow]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteContractLaborById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ContractLabor]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[VendorId],
        DELETED.[ProjectId],
        DELETED.[EmployeeName],
        DELETED.[JobName],
        CONVERT(VARCHAR(10), DELETED.[WorkDate], 120) AS [WorkDate],
        DELETED.[TimeIn],
        DELETED.[TimeOut],
        DELETED.[BreakTime],
        DELETED.[RegularHours],
        DELETED.[OvertimeHours],
        DELETED.[TotalHours],
        DELETED.[HourlyRate],
        DELETED.[Markup],
        DELETED.[TotalAmount],
        DELETED.[SubCostCodeId],
        DELETED.[Description],
        CONVERT(VARCHAR(10), DELETED.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        DELETED.[Status],
        DELETED.[BillLineItemId],
        DELETED.[ImportBatchId],
        DELETED.[SourceFile],
        DELETED.[SourceRow]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- Get last used hourly rate and markup for a vendor (for carry-forward)
GO

CREATE OR ALTER PROCEDURE ReadLastRateForVendor
(
    @VendorId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [HourlyRate],
        [Markup]
    FROM dbo.[ContractLabor]
    WHERE [VendorId] = @VendorId
        AND [HourlyRate] IS NOT NULL
    ORDER BY [WorkDate] DESC, [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO


-- Bulk update status for billing
GO

CREATE OR ALTER PROCEDURE UpdateContractLaborStatusByIds
(
    @Ids NVARCHAR(MAX),  -- Comma-separated list of IDs
    @Status NVARCHAR(20),
    @BillLineItemId BIGINT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ContractLabor]
    SET
        [ModifiedDatetime] = @Now,
        [Status] = @Status,
        [BillLineItemId] = @BillLineItemId
    WHERE [Id] IN (SELECT value FROM STRING_SPLIT(@Ids, ','));

    SELECT @@ROWCOUNT AS [UpdatedCount];

    COMMIT TRANSACTION;
END;
GO


-- Get total hours and allocated hours for employee on a specific date
GO

CREATE OR ALTER PROCEDURE ReadContractLaborDailySummary
(
    @EmployeeName NVARCHAR(200),
    @WorkDate DATE,
    @ExcludeEntryId BIGINT NULL  -- Current entry ID to exclude from "other entries" calculation
)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Get total imported hours and entry count for this employee/date
    DECLARE @TotalImportedHours DECIMAL(10,2);
    DECLARE @EntryCount INT;
    
    SELECT 
        @TotalImportedHours = COALESCE(SUM([TotalHours]), 0),
        @EntryCount = COUNT(*)
    FROM dbo.[ContractLabor]
    WHERE [EmployeeName] = @EmployeeName 
      AND [WorkDate] = @WorkDate;
    
    -- Get already allocated hours from line items (OTHER entries only)
    DECLARE @AllocatedOtherEntries DECIMAL(10,2);
    
    SELECT @AllocatedOtherEntries = COALESCE(SUM(li.[Hours]), 0)
    FROM dbo.[ContractLaborLineItem] li
    INNER JOIN dbo.[ContractLabor] cl ON li.[ContractLaborId] = cl.[Id]
    WHERE cl.[EmployeeName] = @EmployeeName 
      AND cl.[WorkDate] = @WorkDate
      AND (@ExcludeEntryId IS NULL OR cl.[Id] != @ExcludeEntryId);
    
    -- Get allocated hours from line items (THIS entry only)
    DECLARE @AllocatedThisEntry DECIMAL(10,2);
    
    SELECT @AllocatedThisEntry = COALESCE(SUM(li.[Hours]), 0)
    FROM dbo.[ContractLaborLineItem] li
    WHERE li.[ContractLaborId] = @ExcludeEntryId;
    
    SELECT 
        @TotalImportedHours AS [TotalImportedHours],
        @EntryCount AS [EntryCount],
        @AllocatedOtherEntries AS [AllocatedOtherEntries],
        @AllocatedThisEntry AS [AllocatedThisEntry],
        (@TotalImportedHours - @AllocatedOtherEntries - @AllocatedThisEntry) AS [RemainingToAllocate];

    COMMIT TRANSACTION;
END;
GO


-- ============================================
-- ContractLaborLineItem Stored Procedures
-- ============================================

GO

CREATE OR ALTER PROCEDURE CreateContractLaborLineItem
(
    @ContractLaborId BIGINT,
    @LineDate DATE NULL,
    @ProjectId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Hours DECIMAL(6,2) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsBillable BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ContractLaborLineItem] (
        [CreatedDatetime], [ModifiedDatetime], [ContractLaborId], [LineDate], [ProjectId], [SubCostCodeId],
        [Description], [Hours], [Rate], [Markup], [Price], [IsBillable]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ContractLaborId],
        CONVERT(VARCHAR(10), INSERTED.[LineDate], 120) AS [LineDate],
        INSERTED.[ProjectId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Hours],
        INSERTED.[Rate],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsBillable]
    VALUES (
        @Now, @Now, @ContractLaborId, @LineDate, @ProjectId, @SubCostCodeId,
        @Description, @Hours, @Rate, @Markup, @Price, @IsBillable
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborLineItemsByContractLaborId
(
    @ContractLaborId BIGINT
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
        [ContractLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId],
        [SubCostCodeId],
        [Description],
        [Hours],
        [Rate],
        [Markup],
        [Price],
        [IsBillable]
    FROM dbo.[ContractLaborLineItem]
    WHERE [ContractLaborId] = @ContractLaborId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborLineItemById
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
        [ContractLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId],
        [SubCostCodeId],
        [Description],
        [Hours],
        [Rate],
        [Markup],
        [Price],
        [IsBillable]
    FROM dbo.[ContractLaborLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadContractLaborLineItemByPublicId
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
        [ContractLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId],
        [SubCostCodeId],
        [Description],
        [Hours],
        [Rate],
        [Markup],
        [Price],
        [IsBillable]
    FROM dbo.[ContractLaborLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateContractLaborLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @LineDate DATE NULL,
    @ProjectId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Hours DECIMAL(6,2) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsBillable BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ContractLaborLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [LineDate] = @LineDate,
        [ProjectId] = @ProjectId,
        [SubCostCodeId] = @SubCostCodeId,
        [Description] = @Description,
        [Hours] = @Hours,
        [Rate] = @Rate,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsBillable] = @IsBillable
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ContractLaborId],
        CONVERT(VARCHAR(10), INSERTED.[LineDate], 120) AS [LineDate],
        INSERTED.[ProjectId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Hours],
        INSERTED.[Rate],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsBillable]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteContractLaborLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ContractLaborLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ContractLaborId],
        CONVERT(VARCHAR(10), DELETED.[LineDate], 120) AS [LineDate],
        DELETED.[ProjectId],
        DELETED.[SubCostCodeId],
        DELETED.[Description],
        DELETED.[Hours],
        DELETED.[Rate],
        DELETED.[Markup],
        DELETED.[Price],
        DELETED.[IsBillable]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteContractLaborLineItemsByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ContractLaborLineItem]
    WHERE [ContractLaborId] = @ContractLaborId;

    SELECT @@ROWCOUNT AS [DeletedCount];

    COMMIT TRANSACTION;
END;
GO


-- Update only bill-related fields
GO

CREATE OR ALTER PROCEDURE UpdateContractLaborBillInfo
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @BillVendorId BIGINT NULL,
    @BillDate DATE NULL,
    @DueDate DATE NULL,
    @BillNumber NVARCHAR(50) NULL,
    @Status NVARCHAR(20) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ContractLabor]
    SET
        [ModifiedDatetime] = @Now,
        [BillVendorId] = @BillVendorId,
        [BillDate] = @BillDate,
        [DueDate] = @DueDate,
        [BillNumber] = @BillNumber,
        [Status] = CASE WHEN @Status IS NULL THEN [Status] ELSE @Status END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[EmployeeName],
        INSERTED.[JobName],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[TimeIn],
        INSERTED.[TimeOut],
        INSERTED.[BreakTime],
        INSERTED.[RegularHours],
        INSERTED.[OvertimeHours],
        INSERTED.[TotalHours],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[TotalAmount],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodStart], 120) AS [BillingPeriodStart],
        INSERTED.[Status],
        INSERTED.[BillLineItemId],
        INSERTED.[BillVendorId],
        CONVERT(VARCHAR(10), INSERTED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(10), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[BillNumber],
        INSERTED.[ImportBatchId],
        INSERTED.[SourceFile],
        INSERTED.[SourceRow]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO




GO

CREATE OR ALTER PROCEDURE ReadContractLaborByPublicId
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
        [ProjectId],
        [EmployeeName],
        [JobName],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [TimeIn],
        [TimeOut],
        [BreakTime],
        [RegularHours],
        [OvertimeHours],
        [TotalHours],
        [HourlyRate],
        [Markup],
        [TotalAmount],
        [SubCostCodeId],
        [Description],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart],
        [Status],
        [BillLineItemId],
        [BillVendorId],
        CONVERT(VARCHAR(10), [BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(10), [DueDate], 120) AS [DueDate],
        [BillNumber],
        [ImportBatchId],
        [SourceFile],
        [SourceRow]
    FROM dbo.[ContractLabor]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO







