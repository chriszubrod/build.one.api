-- U-200 APPLY: Backfill labor Price / TotalAmount from single-shot CAST to two-shot ROUND parity.
-- MUTATES data. Run 017a PREVIEW first and review its output. Sproc bodies: dbo.time_entry.sql.
-- scripts/run_sql.py executes batches but never fetches result sets or PRINT output — use SSMS,
-- Azure Data Studio, or sqlcmd (any client that shows result sets and messages) to review this.

IF OBJECT_ID('dbo.U200PriceBackfillRestore', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.U200PriceBackfillRestore
    (
        Id                BIGINT         NOT NULL,
        TargetTable       NVARCHAR(40)   NOT NULL,
        OldValue          DECIMAL(18, 2) NULL,
        NewValue          DECIMAL(18, 2) NULL,
        AppliedDatetime   DATETIME2      NOT NULL CONSTRAINT DF_U200PriceBackfillRestore_Applied DEFAULT (SYSUTCDATETIME())
    );
END
GO

-- ─── APPLY: capture pre-image in dbo.U200PriceBackfillRestore ───────────────

UPDATE li
SET li.Price = ROUND(ROUND(li.Hours * li.Rate, 2) * (1 + ISNULL(li.Markup, 0)), 2)
OUTPUT
    deleted.Id,
    N'ContractLaborLineItem',
    deleted.Price,
    inserted.Price
INTO dbo.U200PriceBackfillRestore (Id, TargetTable, OldValue, NewValue)
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

PRINT CONCAT(N'U-200 Target A (ContractLaborLineItem.Price): ', @@ROWCOUNT, N' row(s) updated');
GO

UPDATE li
SET li.Price = ROUND(ROUND(li.Hours * li.Rate, 2) * (1 + ISNULL(li.Markup, 0)), 2)
OUTPUT
    deleted.Id,
    N'EmployeeLaborLineItem',
    deleted.Price,
    inserted.Price
INTO dbo.U200PriceBackfillRestore (Id, TargetTable, OldValue, NewValue)
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

PRINT CONCAT(N'U-200 Target B (EmployeeLaborLineItem.Price): ', @@ROWCOUNT, N' row(s) updated');
GO

UPDATE cl
SET cl.TotalAmount = ROUND(ROUND(cl.TotalHours * cl.HourlyRate, 2) * (1 + ISNULL(cl.Markup, 0)), 2)
OUTPUT
    deleted.Id,
    N'ContractLabor',
    deleted.TotalAmount,
    inserted.TotalAmount
INTO dbo.U200PriceBackfillRestore (Id, TargetTable, OldValue, NewValue)
FROM dbo.ContractLabor cl
WHERE cl.Status IN ('pending_review', 'ready', 'submitted')
  AND cl.TotalHours IS NOT NULL
  AND cl.HourlyRate IS NOT NULL
  AND cl.TotalAmount IS NOT NULL
  AND cl.TotalAmount = CAST(cl.TotalHours * cl.HourlyRate * (1 + ISNULL(cl.Markup, 0)) AS DECIMAL(18, 2))
  AND cl.TotalAmount <> ROUND(ROUND(cl.TotalHours * cl.HourlyRate, 2) * (1 + ISNULL(cl.Markup, 0)), 2);

PRINT CONCAT(N'U-200 Target C (ContractLabor.TotalAmount): ', @@ROWCOUNT, N' row(s) updated');
GO

UPDATE el
SET el.TotalAmount = ROUND(ROUND(el.TotalHours * el.HourlyRate, 2) * (1 + ISNULL(el.Markup, 0)), 2)
OUTPUT
    deleted.Id,
    N'EmployeeLabor',
    deleted.TotalAmount,
    inserted.TotalAmount
INTO dbo.U200PriceBackfillRestore (Id, TargetTable, OldValue, NewValue)
FROM dbo.EmployeeLabor el
WHERE el.Status IN ('pending_review', 'ready')
  AND el.TotalHours IS NOT NULL
  AND el.HourlyRate IS NOT NULL
  AND el.TotalAmount IS NOT NULL
  AND el.TotalAmount = CAST(el.TotalHours * el.HourlyRate * (1 + ISNULL(el.Markup, 0)) AS DECIMAL(18, 2))
  AND el.TotalAmount <> ROUND(ROUND(el.TotalHours * el.HourlyRate, 2) * (1 + ISNULL(el.Markup, 0)), 2);

PRINT CONCAT(N'U-200 Target C (EmployeeLabor.TotalAmount): ', @@ROWCOUNT, N' row(s) updated');
GO

/*
-- ─── ROLLBACK (commented out): restore earliest OldValue per (Id, TargetTable) ─
-- U200PriceBackfillRestore has no PK; re-running APPLY can insert duplicate (Id, TargetTable) rows.
-- rn = 1 is the first capture (AppliedDatetime ASC) — the true pre-backfill original.

SELECT
    Id,
    TargetTable,
    OldValue,
    NewValue
INTO #EarliestRestore
FROM (
    SELECT
        Id,
        TargetTable,
        OldValue,
        NewValue,
        ROW_NUMBER() OVER (
            PARTITION BY Id, TargetTable
            ORDER BY AppliedDatetime ASC, OldValue ASC
        ) AS rn
    FROM dbo.U200PriceBackfillRestore
) ranked
WHERE rn = 1;

UPDATE li
SET li.Price = r.OldValue
FROM dbo.ContractLaborLineItem li
INNER JOIN #EarliestRestore r
    ON r.Id = li.Id AND r.TargetTable = N'ContractLaborLineItem'
WHERE li.Price = r.NewValue;

UPDATE li
SET li.Price = r.OldValue
FROM dbo.EmployeeLaborLineItem li
INNER JOIN #EarliestRestore r
    ON r.Id = li.Id AND r.TargetTable = N'EmployeeLaborLineItem'
WHERE li.Price = r.NewValue;

UPDATE cl
SET cl.TotalAmount = r.OldValue
FROM dbo.ContractLabor cl
INNER JOIN #EarliestRestore r
    ON r.Id = cl.Id AND r.TargetTable = N'ContractLabor'
WHERE cl.TotalAmount = r.NewValue;

UPDATE el
SET el.TotalAmount = r.OldValue
FROM dbo.EmployeeLabor el
INNER JOIN #EarliestRestore r
    ON r.Id = el.Id AND r.TargetTable = N'EmployeeLabor'
WHERE el.TotalAmount = r.NewValue;

DROP TABLE #EarliestRestore;
*/
