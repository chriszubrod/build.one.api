-- Deploy ReadContractLaborByNaturalKey (duplicate detection on import)
GO

CREATE OR ALTER PROCEDURE ReadContractLaborByNaturalKey
(
    @EmployeeName NVARCHAR(255),
    @WorkDate DATE,
    @JobName NVARCHAR(255) NULL,
    @TimeIn NVARCHAR(20) NULL,
    @TimeOut NVARCHAR(20) NULL,
    @Description NVARCHAR(MAX) NULL
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
    WHERE [EmployeeName] = @EmployeeName
        AND [WorkDate] = @WorkDate
        AND ((@JobName IS NULL AND [JobName] IS NULL) OR [JobName] = @JobName)
        AND ((@TimeIn IS NULL AND [TimeIn] IS NULL) OR [TimeIn] = @TimeIn)
        AND ((@TimeOut IS NULL AND [TimeOut] IS NULL) OR [TimeOut] = @TimeOut)
        AND ((@Description IS NULL AND [Description] IS NULL) OR [Description] = @Description)
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO
