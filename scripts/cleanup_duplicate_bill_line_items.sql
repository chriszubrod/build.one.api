-- ============================================================
-- Cleanup duplicate BillLineItems (all projects)
-- Root cause: QBO pull sync recreates QboBillLine records with
-- new internal IDs; the BillLineItemBillLine migration fallback
-- fails when old QboBillLine records are gone, so a new
-- BillLineItem is created alongside the existing one.
--
-- Usage:
--   Set @ProjectId to a specific project ID to scope the cleanup,
--   or leave as NULL to run across all projects.
--
-- Strategy:
--   For each (BillId, SubCostCodeId, Description, Amount) group
--   with more than one row, keep the row that:
--     1. Has a qbo.BillLineItemBillLine mapping (linked to QBO)
--     2. Ties broken by smallest Id (oldest)
--   Delete the rest after removing InvoiceLineItem references.
-- ============================================================

DECLARE @ProjectId BIGINT = 94;  -- Set to a project ID to filter, or NULL for all projects


-- ────────────────────────────────────────────────────────────
-- STEP 1: DIAGNOSTIC — review duplicates before touching data
-- ────────────────────────────────────────────────────────────
SELECT
    p.Name                          AS ProjectName,
    bli.ProjectId,
    bli.BillId,
    b.BillNumber,
    v.Name                          AS VendorName,
    bli.SubCostCodeId,
    scc.Number                      AS SubCostCode,
    LEFT(bli.Description, 60)       AS Description,
    bli.Amount,
    COUNT(*)                        AS DuplicateCount,
    STRING_AGG(CAST(bli.Id AS NVARCHAR(20)), ', ')
        WITHIN GROUP (ORDER BY bli.Id) AS BillLineItemIds,
    STRING_AGG(
        CASE WHEN blbl.BillLineItemId IS NOT NULL THEN 'MAPPED' ELSE 'UNMAPPED' END
        + '(' + CAST(bli.Id AS NVARCHAR(20)) + ')',
        ', '
    ) WITHIN GROUP (ORDER BY bli.Id) AS MappingStatus
FROM dbo.BillLineItem bli
JOIN dbo.Bill b ON b.Id = bli.BillId
JOIN dbo.Vendor v ON v.Id = b.VendorId
LEFT JOIN dbo.Project p ON p.Id = bli.ProjectId
LEFT JOIN dbo.SubCostCode scc ON scc.Id = bli.SubCostCodeId
LEFT JOIN qbo.BillLineItemBillLine blbl ON blbl.BillLineItemId = bli.Id
WHERE (@ProjectId IS NULL OR bli.ProjectId = @ProjectId)
GROUP BY
    p.Name, bli.ProjectId,
    bli.BillId, b.BillNumber, v.Name,
    bli.SubCostCodeId, scc.Number,
    LEFT(bli.Description, 60), bli.Amount
HAVING COUNT(*) > 1
ORDER BY p.Name, b.BillNumber, scc.Number;

GO

-- ────────────────────────────────────────────────────────────
-- STEP 2: IDENTIFY rows to DELETE
-- (rows NOT chosen as the keeper for their duplicate group)
-- ────────────────────────────────────────────────────────────
DECLARE @ProjectId BIGINT = 94;  -- Set to a project ID to filter, or NULL for all projects

WITH Ranked AS (
    SELECT
        bli.Id,
        bli.BillId,
        bli.SubCostCodeId,
        LEFT(bli.Description, 60)   AS DescKey,
        bli.Amount,
        -- Prefer the row that has a QBO mapping; then prefer smallest Id
        ROW_NUMBER() OVER (
            PARTITION BY bli.BillId, bli.SubCostCodeId,
                         LEFT(bli.Description, 60), bli.Amount
            ORDER BY
                CASE WHEN blbl.BillLineItemId IS NOT NULL THEN 0 ELSE 1 END,
                bli.Id ASC
        ) AS rn
    FROM dbo.BillLineItem bli
    LEFT JOIN qbo.BillLineItemBillLine blbl ON blbl.BillLineItemId = bli.Id
    WHERE (@ProjectId IS NULL OR bli.ProjectId = @ProjectId)
)
SELECT Id AS BillLineItemId_ToDelete
FROM Ranked
WHERE rn > 1;   -- rn=1 is the keeper; everything else is a duplicate

GO

-- ────────────────────────────────────────────────────────────
-- STEP 3: CLEANUP — delete the duplicates
-- Wrapped in a transaction so it can be rolled back if needed.
-- ────────────────────────────────────────────────────────────
DECLARE @ProjectId BIGINT = 94;  -- Set to a project ID to filter, or NULL for all projects

BEGIN TRANSACTION;

-- 3a. Remove InvoiceLineItem references (FK constraint)
WITH Ranked AS (
    SELECT
        bli.Id,
        ROW_NUMBER() OVER (
            PARTITION BY bli.BillId, bli.SubCostCodeId,
                         LEFT(bli.Description, 60), bli.Amount
            ORDER BY
                CASE WHEN blbl.BillLineItemId IS NOT NULL THEN 0 ELSE 1 END,
                bli.Id ASC
        ) AS rn
    FROM dbo.BillLineItem bli
    LEFT JOIN qbo.BillLineItemBillLine blbl ON blbl.BillLineItemId = bli.Id
    WHERE (@ProjectId IS NULL OR bli.ProjectId = @ProjectId)
),
ToDelete AS (
    SELECT Id FROM Ranked WHERE rn > 1
)
DELETE ili
FROM dbo.InvoiceLineItem ili
JOIN ToDelete td ON td.Id = ili.BillLineItemId;

-- 3b. Remove the duplicate BillLineItems themselves
WITH Ranked AS (
    SELECT
        bli.Id,
        ROW_NUMBER() OVER (
            PARTITION BY bli.BillId, bli.SubCostCodeId,
                         LEFT(bli.Description, 60), bli.Amount
            ORDER BY
                CASE WHEN blbl.BillLineItemId IS NOT NULL THEN 0 ELSE 1 END,
                bli.Id ASC
        ) AS rn
    FROM dbo.BillLineItem bli
    LEFT JOIN qbo.BillLineItemBillLine blbl ON blbl.BillLineItemId = bli.Id
    WHERE (@ProjectId IS NULL OR bli.ProjectId = @ProjectId)
)
DELETE FROM dbo.BillLineItem
WHERE Id IN (SELECT Id FROM Ranked WHERE rn > 1);

-- Review the row counts, then COMMIT or ROLLBACK
-- ROLLBACK TRANSACTION;
COMMIT TRANSACTION;

GO

-- ────────────────────────────────────────────────────────────
-- STEP 4: VERIFY — should return zero rows after cleanup
-- ────────────────────────────────────────────────────────────
DECLARE @ProjectId BIGINT = 94;  -- Set to a project ID to filter, or NULL for all projects

SELECT
    p.Name                          AS ProjectName,
    bli.BillId,
    b.BillNumber,
    COUNT(*) AS Count
FROM dbo.BillLineItem bli
JOIN dbo.Bill b ON b.Id = bli.BillId
LEFT JOIN dbo.Project p ON p.Id = bli.ProjectId
WHERE (@ProjectId IS NULL OR bli.ProjectId = @ProjectId)
GROUP BY
    p.Name, bli.BillId, b.BillNumber, bli.SubCostCodeId,
    LEFT(bli.Description, 60), bli.Amount
HAVING COUNT(*) > 1
ORDER BY p.Name, b.BillNumber;
