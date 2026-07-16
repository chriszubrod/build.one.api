-- =========================================================================
-- dedupe_project_rows.sql
--
-- Merge 14 duplicate dbo.Project rows back into their 11 canonical originals,
-- then delete the duplicates. Companion fix to
-- CustomerProjectConnector.sync_from_qbo_customer (which previously created
-- a fresh dbo.Project on every QboCustomer pull where qbo.CustomerProject
-- had no mapping row).
--
-- Identified by docs/dedupe-project-rows.md (2026-05-28 audit). Root cause:
-- 10 of 11 keep-Id originals never had a qbo.CustomerProject paired row at
-- original-import time, so the every-4hr scheduler timer
-- (`0 10 */4 * * *` in build.one.scheduler) saw "no mapping for QboCustomer
-- X" and created a fresh local Project. The MR2-MAIN case was repaired
-- individually on 2026-05-28 (see prior session); this migration sweeps
-- the remaining 10 dup-groups + the 3 unmapped orphan dups.
--
-- Special case: SJC has two QboCustomers with identical DisplayName upstream
-- (Id 198 / QboId 1367 and Id 206 / QboId 1444). Keep-Id 129 already maps to
-- 1367; dup-Id 153 mapped to 1444. We DELETE the 153->1444 mapping rather
-- than try to repoint it to 129 (would violate UQ_CustomerProject_ProjectId).
-- The qbo.Customer.Id=206 row stays; the user will reconcile in QBO.
-- =========================================================================

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- Mapping table: dup Project.Id -> canonical keeper Project.Id
-- Sweep grew from 14 → 16 between 2026-05-28 audit and 2026-05-29 run:
-- the every-4hr QBO Customer sync at 00:10 UTC fired in between and created
-- two more dups (TB3 + WVA). Confirms the connector fix needs to land ASAP
-- or this sweep will keep getting re-dirtied.
DECLARE @Map TABLE (DupId BIGINT PRIMARY KEY, KeepId BIGINT NOT NULL);
INSERT INTO @Map (DupId, KeepId) VALUES
    (157,  64),   -- BR-MAIN - 7550C Buffalo Road
    (155, 128),   -- HA - 206 Haverford Ave
    (138,  13),   -- HP2 - 4406 Harding Pike (orphan, no mapping)
    (140,  13),   -- HP2 - 4406 Harding Pike (mapped to QboCustomer 18)
    (156,  55),   -- KA2 - 827 Kirkwood Ave
    (147,  93),   -- MR2-MAIN - 1577 Moran Rd
    (148,  23),   -- OL - 925 Overton Lea
    (146, 132),   -- OL-PH - 925 Overton Lea (orphan, no mapping)
    (149, 132),   -- OL-PH - 925 Overton Lea (mapped to QboCustomer 201)
    (154, 145),   -- OVH - 2031 Overhill Drive (keep=145 per doc decision; 145 has 4 TL + 28 UP, 154 has just 1 ELI)
    (141,  28),   -- SHT - 2012 Sunset Hills (orphan, no mapping)
    (151,  28),   -- SHT - 2012 Sunset Hills (mapped to QboCustomer 44)
    (153, 129),   -- SJC - 1102 Stonewall Jackson (QBO-side dup landmine, see below)
    (150,  79),   -- SSC2 - 5620 S Stanford Ct
    (159,  73),   -- TB3 - 917 Tyne Blvd (created 2026-05-29 00:10 UTC sync)
    (158,  46);   -- WVA - 424 Westview Avenue (created 2026-05-29 00:10 UTC sync)

-- Snapshot before-state for the validation block at the bottom
DECLARE @BeforeProjectCount INT = (SELECT COUNT(*) FROM dbo.Project);
DECLARE @BeforeCpMapped INT = (
    SELECT COUNT(DISTINCT cp.ProjectId)
    FROM qbo.CustomerProject cp
    JOIN @Map m ON cp.ProjectId = m.KeepId
);  -- how many KEEP-Ids already have a qbo.CustomerProject (expect 1: SJC)

-- 1) Repoint transactional + reference FKs (only tables that are non-empty today)
UPDATE x SET ProjectId = m.KeepId
  FROM dbo.BillCreditLineItem x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.BillLineItem x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.Contact x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.ContractLabor x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.ContractLaborLineItem x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.ExpenseLineItem x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.Invoice x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.ProjectAddress x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.Task x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.TimeLog x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM dbo.Workflow x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM ms.DriveItemProject x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM ms.DriveItemProjectExcel x JOIN @Map m ON x.ProjectId = m.DupId;

UPDATE x SET ProjectId = m.KeepId
  FROM ms.DriveItemProjectModule x JOIN @Map m ON x.ProjectId = m.DupId;

-- 2) UserProject: drop dup rows whose (UserId, KeepId) collision already exists,
--    then repoint the rest. Without the DELETE first, the UPDATE would violate
--    the (UserId, ProjectId) natural key.
DELETE up_dup
  FROM dbo.UserProject up_dup
  JOIN @Map m ON up_dup.ProjectId = m.DupId
  WHERE EXISTS (
    SELECT 1 FROM dbo.UserProject up_keep
     WHERE up_keep.UserId = up_dup.UserId
       AND up_keep.ProjectId = m.KeepId
  );

UPDATE up SET ProjectId = m.KeepId
  FROM dbo.UserProject up JOIN @Map m ON up.ProjectId = m.DupId;

-- 3) qbo.CustomerProject: repoint 10 dup mappings -> keepers (heals the
--    long-missing QBO link on each original). Exclude SJC dup-Id 153.
--    Safe vs UQ_CustomerProject_ProjectId: all 10 affected keep-Ids
--    (64, 128, 13, 55, 23, 132, 145, 28, 79, 93) currently have no
--    qbo.CustomerProject row. Safe vs UQ_CustomerProject_QboCustomerId:
--    only the ProjectId side changes.
UPDATE cp SET ProjectId = m.KeepId, ModifiedDatetime = SYSUTCDATETIME()
  FROM qbo.CustomerProject cp
  JOIN @Map m ON cp.ProjectId = m.DupId
  WHERE m.DupId <> 153;

-- 4) SJC dup mapping: delete the spurious link to QboCustomer 206 (QboId 1444).
--    Keep-Id 129 already maps to QboCustomer 198 (QboId 1367); we can't repoint
--    153 -> 129 without violating UQ_CustomerProject_ProjectId. qbo.Customer.Id=206
--    is not deleted; user will merge or rename in QBO and the connector fix
--    (CustomerProjectConnector.sync_from_qbo_customer name-match branch) will
--    surface it as a duplicate_qbo_customer reconciliation issue on the next
--    sync until then.
DELETE FROM qbo.CustomerProject WHERE ProjectId = 153;

-- 5) Pre-delete safety: confirm zero remaining child refs to any dup Id
--    across every column literally named ProjectId, across schemas.
DECLARE @OrphanCount INT = (
    SELECT COUNT(*) FROM (
        SELECT ProjectId FROM dbo.BillCreditLineItem      WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.BillLineItem            WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Contact                 WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ContractLabor           WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ContractLaborLineItem   WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ExpenseLineItem         WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Invoice                 WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.ProjectAddress          WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Task                    WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.TimeLog                 WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.UserProject             WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM dbo.Workflow                WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM ms.DriveItemProject         WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM ms.DriveItemProjectExcel    WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM ms.DriveItemProjectModule   WHERE ProjectId IN (SELECT DupId FROM @Map) UNION ALL
        SELECT ProjectId FROM qbo.CustomerProject         WHERE ProjectId IN (SELECT DupId FROM @Map)
    ) x
);

IF @OrphanCount <> 0
BEGIN
    DECLARE @msg NVARCHAR(200) = CONCAT(
        'dedupe_project_rows: aborting before DELETE — found ',
        @OrphanCount,
        ' remaining child references to dup ProjectIds.'
    );
    RAISERROR(@msg, 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- 6) Delete the dup Project rows.
DELETE p
  FROM dbo.Project p
  JOIN @Map m ON p.Id = m.DupId;

-- 7) Post-state verification
DECLARE @AfterProjectCount INT = (SELECT COUNT(*) FROM dbo.Project);
DECLARE @AfterCpMappedKeepers INT = (
    SELECT COUNT(DISTINCT cp.ProjectId)
    FROM qbo.CustomerProject cp
    JOIN @Map m ON cp.ProjectId = m.KeepId
);
-- Scoped to the names in @Map only: counts how many of the names we
-- intended to dedupe STILL have >1 active row. If any other unrelated
-- dup-name groups exist in dbo.Project (e.g. created by the in-flight
-- buggy QBO connector between the audit and this run), they don't trip
-- this assertion — they'll be cleaned up by the next sweep once the
-- connector fix is deployed.
DECLARE @MapNameGroupsStillDup INT = (
    SELECT COUNT(*)
    FROM (
        SELECT p.Name
        FROM dbo.Project p
        WHERE p.Name IN (SELECT k.Name
                          FROM dbo.Project k
                          JOIN @Map m ON m.KeepId = k.Id)
        GROUP BY p.Name
        HAVING COUNT(*) > 1
    ) g
);
DECLARE @DupRowsInMap INT = (SELECT COUNT(*) FROM @Map);   -- expected 16
DECLARE @ExpectedMappedKeepers INT = (
    -- All keepers picked up via the repoint (excluding SJC which we DELETE
    -- the spurious mapping for), PLUS any keepers that already had a mapping.
    SELECT COUNT(DISTINCT m.KeepId)
    FROM @Map m
    WHERE m.DupId <> 153   -- SJC dup mapping gets DELETEd, not repointed
) + (
    SELECT COUNT(*) FROM qbo.CustomerProject cp
    JOIN @Map m ON cp.ProjectId = m.KeepId
    WHERE m.KeepId = 129   -- SJC keep already had its own mapping pre-migration
);

PRINT CONCAT('Projects before: ', @BeforeProjectCount, ' / after: ', @AfterProjectCount,
             ' / expected delta: -', @DupRowsInMap,
             ' / actual delta: ', @AfterProjectCount - @BeforeProjectCount);
PRINT CONCAT('Keep-Ids with qbo.CustomerProject mapping before: ', @BeforeCpMapped,
             ' / after: ', @AfterCpMappedKeepers,
             ' / expected after: ', @ExpectedMappedKeepers);
PRINT CONCAT('Name groups from @Map still showing duplicates: ', @MapNameGroupsStillDup, ' (expected 0)');

IF (@AfterProjectCount - @BeforeProjectCount) <> -@DupRowsInMap
   OR @MapNameGroupsStillDup <> 0
   OR @AfterCpMappedKeepers <> @ExpectedMappedKeepers
BEGIN
    RAISERROR('dedupe_project_rows: post-state verification failed — rolling back.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

COMMIT TRANSACTION;
PRINT 'dedupe_project_rows: COMMITTED.';
