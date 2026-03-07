-- Diagnostic: Check what AccountRefName values exist and how many lines match
SELECT
    pl.[AccountRefName],
    COUNT(*) AS [LineCount]
FROM [qbo].[PurchaseLine] pl
INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
GROUP BY pl.[AccountRefName]
ORDER BY COUNT(*) DESC;
GO

-- Total purchase lines vs linked vs unlinked
SELECT
    COUNT(*) AS [TotalLines],
    SUM(CASE WHEN pleli.[Id] IS NOT NULL THEN 1 ELSE 0 END) AS [LinkedLines],
    SUM(CASE WHEN pleli.[Id] IS NULL THEN 1 ELSE 0 END) AS [UnlinkedLines]
FROM [qbo].[PurchaseLine] pl
LEFT JOIN [qbo].[PurchaseLineExpenseLineItem] pleli ON pl.[Id] = pleli.[QboPurchaseLineId];
GO
