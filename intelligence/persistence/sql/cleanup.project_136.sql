-- Cleanup phantom dbo.Project Id=136 ("2031 Overhill").
--
-- One-off orphan — almost certainly an early QBO Customer sync attempt that
-- created a Project with the raw QboCustomer DisplayName before the parent
-- customer mapping resolved, leaving:
--   - Name = "2031 Overhill" (no "OVH" prefix, no full address)
--   - Abbreviation = NULL
--   - CustomerId = 1 ("0 - Temp Customer" — the placeholder)
--   - No qbo.CustomerProject mapping
--   - Created 2026-05-12 16:10 by user 17 (the COALESCE-fallback signature
--     of the scheduler-triggered QBO sync running with no actor context)
--
-- The canonical OVH is Project Id 145 ("OVH - 2031 Overhill Drive"), which
-- has the QBO mapping (cp.Id=150 -> QboCustomer 202 / QboId 1429) and the
-- real financial activity (ELI, CLLI, TL refs). Id 136 has only 3
-- UserProject rows and zero references on every other FK-bearing column
-- named ProjectId.
--
-- Idempotent — safe to re-run. Short-circuits to a no-op when Project 136
-- is already gone. Re-verifies zero non-UP references before delete;
-- aborts with RAISERROR + ROLLBACK on any non-zero ref.
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/cleanup.project_136.sql

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- ─── 0. Short-circuit when already clean ────────────────────────────────
IF NOT EXISTS (SELECT 1 FROM dbo.Project WHERE Id = 136)
BEGIN
    PRINT 'cleanup.project_136: nothing to do — Project 136 already deleted.';
    COMMIT TRANSACTION;
    RETURN;
END;

-- ─── 1. Re-verify zero refs across every column named ProjectId ────────
--   EXCEPT dbo.UserProject (handled explicitly in step 2).
DECLARE @NonUpRefs INT = (
    SELECT COUNT(*) FROM (
        SELECT ProjectId FROM dbo.BillCreditLineItem      WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.BillLineItem            WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.Contact                 WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.ContractLabor           WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.ContractLaborLineItem   WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.EmployeeLabor           WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.EmployeeLaborLineItem   WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.EmployeeProjectRate     WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.ExpenseLineItem         WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.Invoice                 WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.MsMessageProject        WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.ProjectAddress          WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.Task                    WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.TimeEntry               WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.TimeLog                 WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.VendorProjectRate       WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM dbo.Workflow                WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM ms.DriveItemProject         WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM ms.DriveItemProjectExcel    WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM ms.DriveItemProjectModule   WHERE ProjectId = 136 UNION ALL
        SELECT ProjectId FROM qbo.CustomerProject         WHERE ProjectId = 136
    ) refs
);
IF @NonUpRefs <> 0
BEGIN
    DECLARE @msg NVARCHAR(200) = CONCAT(
        'cleanup.project_136: aborting — found ',
        @NonUpRefs,
        ' non-UserProject child references to Project 136. Investigate before retry.'
    );
    RAISERROR(@msg, 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- ─── 2. Delete the 3 UserProject rows ──────────────────────────────────
DECLARE @UpDeleted INT;
DELETE FROM dbo.UserProject WHERE ProjectId = 136;
SET @UpDeleted = @@ROWCOUNT;

-- ─── 3. Delete the Project ─────────────────────────────────────────────
DECLARE @ProjectDeleted INT;
DELETE FROM dbo.Project WHERE Id = 136;
SET @ProjectDeleted = @@ROWCOUNT;

-- ─── 4. Summary ────────────────────────────────────────────────────────
PRINT CONCAT('cleanup.project_136: UserProject rows deleted=', @UpDeleted,
             ' / dbo.Project rows deleted=', @ProjectDeleted);

IF @ProjectDeleted <> 1
BEGIN
    RAISERROR('cleanup.project_136: expected exactly 1 Project deleted; aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

COMMIT TRANSACTION;
PRINT 'cleanup.project_136: COMMITTED.';
