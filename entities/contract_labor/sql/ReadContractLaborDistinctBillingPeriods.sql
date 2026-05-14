GO

CREATE OR ALTER PROCEDURE ReadContractLaborDistinctBillingPeriods
AS
BEGIN
    SET NOCOUNT ON;

    SELECT DISTINCT
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120) AS [BillingPeriodStart]
    FROM dbo.[ContractLabor]
    WHERE [BillingPeriodStart] IS NOT NULL
    ORDER BY [BillingPeriodStart] DESC;
END;
GO
