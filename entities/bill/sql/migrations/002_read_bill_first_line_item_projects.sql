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
