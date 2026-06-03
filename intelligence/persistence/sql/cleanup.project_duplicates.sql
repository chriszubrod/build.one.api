-- Cleanup confirmed phantom dbo.Project duplicate rows.
--
-- Idempotent — safe to re-run. Each phantom is verified to have zero
-- references across every FK-bearing table before deletion; the script
-- aborts (RAISERROR + RETURN) on any non-zero reference so we never
-- orphan child data.
--
-- Phantoms handled here (one pair, already audited):
--   Id 160 "HA - 206 Haverford Ave"      (keep Id 128, abbr=HA)
--   Id 166 "WVA - 424 Westview Avenue"   (keep Id 46,  abbr=WVA)
--
-- Both were created post-deploy by the recurring 4-hourly QBO Customer
-- sync. Each carries exactly one row in qbo.CustomerProject and zero
-- transactional/child rows. To avoid leaving a dangling
-- qbo.CustomerProject mapping at a deleted Project.Id, this script
-- REPOINTS each mapping to its canonical keeper before deletion (the
-- keepers currently have no mapping, so the UQ_CustomerProject_ProjectId
-- constraint is satisfied).
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/cleanup.project_duplicates.sql

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- Mapping (DupId, KeepId)
DECLARE @Map TABLE (DupId BIGINT PRIMARY KEY, KeepId BIGINT NOT NULL);
INSERT INTO @Map (DupId, KeepId) VALUES
    (160, 128),  -- HA
    (166,  46);  -- WVA

-- ─── 0. Short-circuit when there's nothing to do (idempotency) ──────────
-- If both phantom rows are gone, treat as already-clean and exit
-- successfully without further checks. This keeps re-runs from tripping
-- the pre-flight guard at step 2 (which expects the keepers to NOT
-- already hold the mapping — true only on the first run).
DECLARE @PhantomsPresent INT = (SELECT COUNT(*) FROM dbo.Project WHERE Id IN (SELECT DupId FROM @Map));
IF @PhantomsPresent = 0
BEGIN
    PRINT 'cleanup.project_duplicates: nothing to do — phantom rows already deleted.';
    COMMIT TRANSACTION;
    RETURN;
END;

-- ─── 1. Reference re-verification across every column named ProjectId ───
-- User-listed activity tables PLUS every other FK-bearing table found via
-- sys.foreign_key_columns. RAISERROR + ROLLBACK if any row exists.
DECLARE @RefCount INT = (
    SELECT COUNT(*) FROM (
        SELECT ProjectId FROM dbo.BillLineItem            WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.BillCreditLineItem      WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ExpenseLineItem         WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Invoice                 WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ContractLabor           WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ContractLaborLineItem   WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.TimeEntry               WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.TimeLog                 WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.UserProject             WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Contact                 WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Task                    WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Workflow                WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ProjectAddress          WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.MsMessageProject        WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.EmployeeLabor           WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.EmployeeLaborLineItem   WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.EmployeeProjectRate     WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.VendorProjectRate       WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM ms.DriveItemProject         WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM ms.DriveItemProjectExcel    WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM ms.DriveItemProjectModule   WHERE ProjectId IN (SELECT DupId FROM @Map)
    ) AS refs
);

IF @RefCount <> 0
BEGIN
    DECLARE @msg NVARCHAR(200) = CONCAT(
        'cleanup.project_duplicates: aborting — found ',
        @RefCount,
        ' child references to phantom Project Ids (160, 166). Investigate before retry.'
    );
    RAISERROR(@msg, 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- ─── 2. Pre-flight for qbo.CustomerProject repoint ──────────────────────
-- Keepers must NOT already hold a mapping (UQ_CustomerProject_ProjectId).
-- If a keeper already has one, abort instead of silently colliding.
DECLARE @KeeperWithMapping INT = (
    SELECT COUNT(*)
    FROM qbo.CustomerProject cp
    JOIN @Map m ON cp.ProjectId = m.KeepId
);
IF @KeeperWithMapping <> 0
BEGIN
    RAISERROR('cleanup.project_duplicates: at least one keeper Project already has a qbo.CustomerProject mapping; repoint would violate UQ_CustomerProject_ProjectId. Aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- ─── 3. Repoint qbo.CustomerProject from dup → keeper ───────────────────
-- Idempotent: re-run finds zero rows to update because the previous run
-- already moved them (or because the dup Project + its mapping are gone).
DECLARE @MappingRepointed INT;
UPDATE cp
   SET cp.ProjectId = m.KeepId, cp.ModifiedDatetime = SYSUTCDATETIME()
  FROM qbo.CustomerProject cp
  JOIN @Map m ON cp.ProjectId = m.DupId;
SET @MappingRepointed = @@ROWCOUNT;

-- ─── 4. DELETE the phantoms ─────────────────────────────────────────────
DECLARE @Deleted INT;
DELETE p
  FROM dbo.Project p
  JOIN @Map m ON p.Id = m.DupId;
SET @Deleted = @@ROWCOUNT;

-- ─── 5. Summary ─────────────────────────────────────────────────────────
DECLARE @PhantomSetSize INT = (SELECT COUNT(*) FROM @Map);
PRINT CONCAT('cleanup.project_duplicates: ',
             'qbo.CustomerProject rows repointed=', @MappingRepointed,
             ' / dbo.Project rows deleted=', @Deleted,
             ' / phantom-set size=', @PhantomSetSize);

-- Post-state guard: phantom rows must be gone after a fresh run; for an
-- idempotent re-run after a clean prior run both @MappingRepointed and
-- @Deleted will be 0 and that's fine.
DECLARE @StillPresent INT = (SELECT COUNT(*) FROM dbo.Project WHERE Id IN (SELECT DupId FROM @Map));
IF @StillPresent <> 0
BEGIN
    RAISERROR('cleanup.project_duplicates: phantom Project rows still present after DELETE; aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

COMMIT TRANSACTION;
PRINT 'cleanup.project_duplicates: COMMITTED.';
