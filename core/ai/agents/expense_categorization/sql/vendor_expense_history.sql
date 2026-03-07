-- =============================================================================
-- Vendor Expense History: Stored procedures for querying historical
-- SubCostCode/Project assignment patterns by vendor.
-- =============================================================================


CREATE OR ALTER PROCEDURE ReadVendorExpenseHistory
(
    @VendorId BIGINT,
    @Limit INT = 20
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Limit)
        eli.[SubCostCodeId],
        scc.[Number] AS [SubCostCodeNumber],
        scc.[Name] AS [SubCostCodeName],
        eli.[ProjectId],
        p.[Name] AS [ProjectName],
        p.[Abbreviation] AS [ProjectAbbreviation],
        COUNT(*) AS [UsageCount],
        SUM(eli.[Amount]) AS [TotalAmount],
        MAX(e.[ExpenseDate]) AS [MostRecentDate],
        AVG(eli.[Amount]) AS [AvgAmount],
        (
            SELECT TOP 3 eli2.[Description] + ' | '
            FROM dbo.[ExpenseLineItem] eli2
            INNER JOIN dbo.[Expense] e2 ON eli2.[ExpenseId] = e2.[Id]
            WHERE e2.[VendorId] = @VendorId
              AND ISNULL(eli2.[SubCostCodeId], 0) = ISNULL(eli.[SubCostCodeId], 0)
              AND ISNULL(eli2.[ProjectId], 0) = ISNULL(eli.[ProjectId], 0)
              AND eli2.[Description] IS NOT NULL
              AND eli2.[Description] <> ''
            ORDER BY e2.[ExpenseDate] DESC
            FOR XML PATH('')
        ) AS [SampleDescriptions]
    FROM dbo.[ExpenseLineItem] eli
    INNER JOIN dbo.[Expense] e ON eli.[ExpenseId] = e.[Id]
    LEFT JOIN dbo.[SubCostCode] scc ON eli.[SubCostCodeId] = scc.[Id]
    LEFT JOIN dbo.[Project] p ON eli.[ProjectId] = p.[Id]
    WHERE e.[VendorId] = @VendorId
      AND eli.[SubCostCodeId] IS NOT NULL
    GROUP BY
        eli.[SubCostCodeId],
        scc.[Number],
        scc.[Name],
        eli.[ProjectId],
        p.[Name],
        p.[Abbreviation]
    ORDER BY COUNT(*) DESC, MAX(e.[ExpenseDate]) DESC;
END;
GO
