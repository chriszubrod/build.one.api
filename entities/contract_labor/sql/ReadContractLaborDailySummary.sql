GO

CREATE OR ALTER PROCEDURE ReadContractLaborDailySummary
(
    @EmployeeName NVARCHAR(200),
    @WorkDate DATE,
    @ExcludeEntryId BIGINT NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @TotalImportedHours DECIMAL(10,2);
    DECLARE @EntryCount INT;

    SELECT
        @TotalImportedHours = COALESCE(SUM([TotalHours]), 0),
        @EntryCount = COUNT(*)
    FROM dbo.[ContractLabor]
    WHERE [EmployeeName] = @EmployeeName
      AND [WorkDate] = @WorkDate;

    DECLARE @AllocatedOtherEntries DECIMAL(10,2);

    SELECT @AllocatedOtherEntries = COALESCE(SUM(li.[Hours]), 0)
    FROM dbo.[ContractLaborLineItem] li
    INNER JOIN dbo.[ContractLabor] cl ON li.[ContractLaborId] = cl.[Id]
    WHERE cl.[EmployeeName] = @EmployeeName
      AND cl.[WorkDate] = @WorkDate
      AND (@ExcludeEntryId IS NULL OR cl.[Id] != @ExcludeEntryId);

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
END;
GO
