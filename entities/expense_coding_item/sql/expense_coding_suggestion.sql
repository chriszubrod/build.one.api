-- Expense coding suggestion read-model sprocs (U-005 Phase B).
-- Standalone file — do NOT merge into dbo.project.sql (drift landmine).
--
-- Run: python scripts/run_sql.py entities/expense_coding_item/sql/expense_coding_suggestion.sql

GO

CREATE OR ALTER PROCEDURE ReadProjectByAbbreviation
(
    @Abbreviation NVARCHAR(20)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.[Id],
        p.[PublicId],
        p.[Name],
        p.[Abbreviation]
    FROM dbo.[Project] p
    WHERE LOWER(p.[Abbreviation]) = LOWER(@Abbreviation)
      AND p.[Status] = N'active';
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorDominantSubCostCode
(
    @VendorId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH Coded AS (
        SELECT eli.[SubCostCodeId]
        FROM dbo.[ExpenseLineItem] eli
        INNER JOIN dbo.[Expense] e ON eli.[ExpenseId] = e.[Id]
        WHERE e.[VendorId] = @VendorId
          AND e.[IsDraft] = 0
          AND eli.[IsDraft] = 0        -- guard both: finalization flips parent before lines
          AND eli.[SubCostCodeId] IS NOT NULL
    ),
    Counts AS (
        SELECT
            [SubCostCodeId],
            COUNT(*) AS [TopCount]
        FROM Coded
        GROUP BY [SubCostCodeId]
    ),
    Totals AS (
        SELECT COUNT(*) AS [TotalCount] FROM Coded
    )
    SELECT TOP 1
        c.[SubCostCodeId],
        scc.[Number],
        scc.[Name],
        c.[TopCount],
        t.[TotalCount]
    FROM Counts c
    CROSS JOIN Totals t
    INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = c.[SubCostCodeId]
    WHERE t.[TotalCount] >= 3
    ORDER BY c.[TopCount] DESC, c.[SubCostCodeId] ASC;
END;
GO
