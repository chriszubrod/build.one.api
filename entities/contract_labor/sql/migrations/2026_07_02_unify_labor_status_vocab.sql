-- =============================================================================
-- 2026-07-02 — Unify status vocabulary across all entities. Labor first.
--
-- Old → New mapping (in-place rename, no history backfill per Chris' call):
--   ContractLabor.Status:
--     pending_review → draft
--     ready          → approved
--     billed         → completed
--     (submitted / in_review / declined are new valid values; no existing
--      rows carry them since the old machine didn't have those states)
--
--   EmployeeLabor.Status:
--     pending_review → draft
--     ready          → approved
--     invoiced       → completed
--
-- Same 6-value canonical vocabulary applies to all future entities:
--   draft → submitted → in_review → approved → completed
--                                ↘ declined ↗
--
-- Schema defaults on Create sprocs are also flipped to 'draft'. Downstream
-- filters in the app (Generate Bills reads 'approved', etc.) are updated
-- in the code change that lands with this migration.
--
-- Prod row counts before migration (2026-07-02):
--   ContractLabor: pending_review 581, ready 13, billed 338
--   EmployeeLabor: (empty)
-- =============================================================================

SET NOCOUNT ON;
GO

-- ============================================================================
-- ContractLabor
-- ============================================================================
UPDATE dbo.[ContractLabor] SET [Status] = 'draft'     WHERE [Status] = 'pending_review';
PRINT CONCAT('ContractLabor pending_review → draft: ', @@ROWCOUNT);

UPDATE dbo.[ContractLabor] SET [Status] = 'approved'  WHERE [Status] = 'ready';
PRINT CONCAT('ContractLabor ready → approved:       ', @@ROWCOUNT);

UPDATE dbo.[ContractLabor] SET [Status] = 'completed' WHERE [Status] = 'billed';
PRINT CONCAT('ContractLabor billed → completed:     ', @@ROWCOUNT);

-- Flip column default from 'pending_review' → 'draft'. The default is
-- named per SQL Server convention (DF_<Table>_<Column>); we look it up
-- rather than assume the name so this migration is idempotent on redeploy.
DECLARE @DfName SYSNAME;
SELECT @DfName = dc.name
FROM sys.default_constraints dc
INNER JOIN sys.columns c ON c.default_object_id = dc.object_id
WHERE c.object_id = OBJECT_ID('dbo.ContractLabor')
  AND c.name = 'Status';
IF @DfName IS NOT NULL
BEGIN
    DECLARE @sql NVARCHAR(MAX) = N'ALTER TABLE dbo.[ContractLabor] DROP CONSTRAINT ' + QUOTENAME(@DfName);
    EXEC sp_executesql @sql;
END
ALTER TABLE dbo.[ContractLabor]
    ADD CONSTRAINT [DF_ContractLabor_Status] DEFAULT ('draft') FOR [Status];
PRINT 'ContractLabor.Status default → ''draft''';

-- ============================================================================
-- EmployeeLabor (empty in prod today; migrate anyway for schema consistency)
-- ============================================================================
UPDATE dbo.[EmployeeLabor] SET [Status] = 'draft'     WHERE [Status] = 'pending_review';
PRINT CONCAT('EmployeeLabor pending_review → draft: ', @@ROWCOUNT);

UPDATE dbo.[EmployeeLabor] SET [Status] = 'approved'  WHERE [Status] = 'ready';
PRINT CONCAT('EmployeeLabor ready → approved:       ', @@ROWCOUNT);

UPDATE dbo.[EmployeeLabor] SET [Status] = 'completed' WHERE [Status] = 'invoiced';
PRINT CONCAT('EmployeeLabor invoiced → completed:   ', @@ROWCOUNT);

DECLARE @DfName2 SYSNAME;
SELECT @DfName2 = dc.name
FROM sys.default_constraints dc
INNER JOIN sys.columns c ON c.default_object_id = dc.object_id
WHERE c.object_id = OBJECT_ID('dbo.EmployeeLabor')
  AND c.name = 'Status';
IF @DfName2 IS NOT NULL
BEGIN
    DECLARE @sql2 NVARCHAR(MAX) = N'ALTER TABLE dbo.[EmployeeLabor] DROP CONSTRAINT ' + QUOTENAME(@DfName2);
    EXEC sp_executesql @sql2;
END
ALTER TABLE dbo.[EmployeeLabor]
    ADD CONSTRAINT [DF_EmployeeLabor_Status] DEFAULT ('draft') FOR [Status];
PRINT 'EmployeeLabor.Status default → ''draft''';

-- ============================================================================
-- Verify (any leftover legacy values would print here)
-- ============================================================================
SELECT
    'ContractLabor' AS Entity,
    [Status]        AS Val,
    COUNT(*)        AS N
FROM dbo.[ContractLabor]
GROUP BY [Status]
UNION ALL
SELECT
    'EmployeeLabor' AS Entity,
    [Status]        AS Val,
    COUNT(*)        AS N
FROM dbo.[EmployeeLabor]
GROUP BY [Status]
ORDER BY Entity, Val;
GO
