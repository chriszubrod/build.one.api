-- U-200 PREVIEW (READ-ONLY): rows that would be updated by the two-shot labor price backfill.
-- Safe to run any time — creates and modifies nothing. Run and review output BEFORE 017b APPLY.
-- scripts/run_sql.py executes batches but never fetches result sets or PRINT output — use SSMS,
-- Azure Data Studio, or sqlcmd (any client that shows result sets and messages) to review this.

-- Target A: ContractLaborLineItem.Price
SELECT
    li.Id,
    li.Price AS CurrentPrice,
    ROUND(ROUND(li.Hours * li.Rate, 2) * (1 + ISNULL(li.Markup, 0)), 2) AS NewPrice
FROM dbo.ContractLaborLineItem li
INNER JOIN dbo.ContractLabor cl ON cl.Id = li.ContractLaborId
WHERE cl.Status IN ('pending_review', 'ready', 'submitted')
  -- Line already pushed to a bill is frozen regardless of parent status.
  AND li.BillLineItemId IS NULL
  AND li.Hours IS NOT NULL
  AND li.Rate IS NOT NULL
  AND li.Price IS NOT NULL
  AND li.Price = CAST(li.Hours * li.Rate * (1 + ISNULL(li.Markup, 0)) AS DECIMAL(18, 2))
  AND li.Price <> ROUND(ROUND(li.Hours * li.Rate, 2) * (1 + ISNULL(li.Markup, 0)), 2);
GO

-- Target B: EmployeeLaborLineItem.Price
SELECT
    li.Id,
    li.Price AS CurrentPrice,
    ROUND(ROUND(li.Hours * li.Rate, 2) * (1 + ISNULL(li.Markup, 0)), 2) AS NewPrice
FROM dbo.EmployeeLaborLineItem li
INNER JOIN dbo.EmployeeLabor el ON el.Id = li.EmployeeLaborId
WHERE el.Status IN ('pending_review', 'ready')
  -- Line already pushed to an invoice is frozen regardless of parent status.
  AND li.InvoiceLineItemId IS NULL
  AND li.Hours IS NOT NULL
  AND li.Rate IS NOT NULL
  AND li.Price IS NOT NULL
  AND li.Price = CAST(li.Hours * li.Rate * (1 + ISNULL(li.Markup, 0)) AS DECIMAL(18, 2))
  AND li.Price <> ROUND(ROUND(li.Hours * li.Rate, 2) * (1 + ISNULL(li.Markup, 0)), 2);
GO

-- Target C (ContractLabor header): TotalAmount
SELECT
    cl.Id,
    cl.TotalAmount AS CurrentTotalAmount,
    ROUND(ROUND(cl.TotalHours * cl.HourlyRate, 2) * (1 + ISNULL(cl.Markup, 0)), 2) AS NewTotalAmount
FROM dbo.ContractLabor cl
WHERE cl.Status IN ('pending_review', 'ready', 'submitted')
  AND cl.TotalHours IS NOT NULL
  AND cl.HourlyRate IS NOT NULL
  AND cl.TotalAmount IS NOT NULL
  AND cl.TotalAmount = CAST(cl.TotalHours * cl.HourlyRate * (1 + ISNULL(cl.Markup, 0)) AS DECIMAL(18, 2))
  AND cl.TotalAmount <> ROUND(ROUND(cl.TotalHours * cl.HourlyRate, 2) * (1 + ISNULL(cl.Markup, 0)), 2);
GO

-- Target C (EmployeeLabor header): TotalAmount
SELECT
    el.Id,
    el.TotalAmount AS CurrentTotalAmount,
    ROUND(ROUND(el.TotalHours * el.HourlyRate, 2) * (1 + ISNULL(el.Markup, 0)), 2) AS NewTotalAmount
FROM dbo.EmployeeLabor el
WHERE el.Status IN ('pending_review', 'ready')
  AND el.TotalHours IS NOT NULL
  AND el.HourlyRate IS NOT NULL
  AND el.TotalAmount IS NOT NULL
  AND el.TotalAmount = CAST(el.TotalHours * el.HourlyRate * (1 + ISNULL(el.Markup, 0)) AS DECIMAL(18, 2))
  AND el.TotalAmount <> ROUND(ROUND(el.TotalHours * el.HourlyRate, 2) * (1 + ISNULL(el.Markup, 0)), 2);
GO
